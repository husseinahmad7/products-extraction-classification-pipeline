"""Scheduled beta work is admitted atomically, before queue/evidence growth."""

from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from product_pipeline.contracts import Limits
from product_pipeline.errors import PipelineError
from product_pipeline.server.api import create_app
from product_pipeline.server.db import (
    ApprovedDomain,
    Audit,
    BudgetUsage,
    Database,
    DatasetHead,
    Job,
    Resource,
    Schedule,
    now,
)
from product_pipeline.server.public_beta import GLOBAL, admit_schedule, guard_claim
from product_pipeline.server.settings import Settings
from product_pipeline.server.worker import Worker
from product_pipeline.storage import LocalStore


def seed_schedule(db, workspace, source, recipe, interval=900):
    schedule_id = str(uuid4())
    with db.sessions.begin() as session:
        session.add(
            Resource(workspace=workspace, kind="source", id=source.id, data=source.model_dump())
        )
        session.add(
            Resource(
                workspace=workspace, kind="recipe", id="recipe", data={"spec": recipe.model_dump()}
            )
        )
        session.add(
            Resource(
                workspace=workspace,
                kind="active_recipe",
                id=source.id,
                data={"recipe_id": "recipe"},
            )
        )
        session.add(DatasetHead(workspace=workspace, source_id=source.id))
        session.add(
            Schedule(
                id=schedule_id,
                workspace=workspace,
                source_id=source.id,
                recipe_id="recipe",
                interval_seconds=interval,
                next_due=now() - timedelta(seconds=1),
            )
        )
    return schedule_id


@pytest.fixture
def admission_system(tmp_path, source, recipe):
    db = Database(f"sqlite:///{tmp_path / 'admission.db'}")
    db.initialize()
    settings = Settings(
        auth_mode="neon",
        neon_auth_issuer="https://auth.example.com/auth",
        neon_auth_jwks_url="https://auth.example.com/auth/.well-known/jwks.json",
        artifact_root=str(tmp_path / "objects"),
    )
    source = source.model_copy(
        update={
            "limits": Limits(
                max_pages=2,
                max_records=1000,
                max_bytes=100_000,
                deadline_seconds=60,
                request_timeout_seconds=20,
            ),
            "min_interval_seconds": 1,
        }
    )
    with db.sessions.begin() as session:
        session.add(
            ApprovedDomain(
                domain="example.com",
                enabled=True,
                actor="operator",
                reason="Synthetic public-source approval",
            )
        )
    schedule_id = seed_schedule(db, "alpha", source, recipe)
    yield db, settings, source, recipe, schedule_id
    db.engine.dispose()


def budgets(db):
    with db.sessions() as session:
        return {
            (row.scope, row.period): dict(row.counters)
            for row in session.scalars(select(BudgetUsage))
        }


def queued_rows(db):
    with db.sessions() as session:
        return list(session.scalars(select(Job))), list(
            session.scalars(select(Resource).where(Resource.kind == "run"))
        )


@pytest.mark.parametrize("scope", ["alpha", GLOBAL])
def test_exhausted_jobs_pause_schedule_without_queue_or_repeated_dispatch(admission_system, scope):
    db, settings, _, _, schedule_id = admission_system
    cap = settings.beta_max_jobs if scope == "alpha" else settings.beta_global_max_jobs
    with db.sessions.begin() as session:
        session.add(
            BudgetUsage(scope=scope, period=now().strftime("%Y-%m"), counters={"jobs": cap})
        )
    before = budgets(db)
    admission = Mock(side_effect=lambda session, row: admit_schedule(session, row, settings))
    worker = Worker(
        db,
        store_factory=lambda workspace: settings.store(db, workspace=workspace),
        schedule_admission=admission,
        claim_guard=lambda claim: guard_claim(db, claim, settings),
    )
    report = worker.run_batch(max_seconds=600)
    assert report.processed == 0 and report.reason == "idle"
    for _ in range(3):
        assert db.dispatch_due_schedules(admission=admission) == 0
    assert admission.call_count == 1 and queued_rows(db) == ([], [])
    assert budgets(db) == before
    with db.sessions() as session:
        row = session.get(Schedule, schedule_id)
        assert row.state == "paused" and row.version == 2 and row.last_run_id is None
        events = list(session.scalars(select(Audit).where(Audit.target == schedule_id)))
        assert len(events) == 1 and events[0].details["reason"] == "QUOTA_EXCEEDED"


@pytest.mark.parametrize("counter", ["runtime_seconds", "metadata_bytes"])
def test_late_budget_denial_rolls_back_global_and_tenant_reservations(admission_system, counter):
    db, settings, _, _, schedule_id = admission_system
    period = "total" if counter == "metadata_bytes" else now().strftime("%Y-%m")
    cap = settings.beta_metadata_quota_bytes if counter == "metadata_bytes" else 1800
    with db.sessions.begin() as session:
        session.add(BudgetUsage(scope="alpha", period=period, counters={counter: cap}))
    before = budgets(db)
    assert (
        db.dispatch_due_schedules(
            admission=lambda session, row: admit_schedule(session, row, settings)
        )
        == 0
    )
    assert budgets(db) == before and queued_rows(db) == ([], [])
    with db.sessions() as session:
        assert session.get(Schedule, schedule_id).state == "paused"


@pytest.mark.parametrize("denial", ["revoked", "limits", "interval"])
def test_source_reapproval_and_limits_are_checked_before_scheduled_queue_creation(
    admission_system, denial
):
    db, settings, source, _, schedule_id = admission_system
    with db.sessions.begin() as session:
        if denial == "revoked":
            session.get(ApprovedDomain, "example.com").enabled = False
        elif denial == "limits":
            changed = source.model_copy(
                update={"limits": source.limits.model_copy(update={"max_pages": 11})}
            )
            session.get(Resource, ("alpha", "source", source.id)).data = changed.model_dump()
        else:
            session.get(Schedule, schedule_id).interval_seconds = 60
    assert (
        db.dispatch_due_schedules(
            admission=lambda session, row: admit_schedule(session, row, settings)
        )
        == 0
    )
    assert not budgets(db) and queued_rows(db) == ([], [])
    assert (
        db.dispatch_due_schedules(
            admission=lambda session, row: admit_schedule(session, row, settings)
        )
        == 0
    )
    with db.sessions() as session:
        row = session.get(Schedule, schedule_id)
        assert row.state == "paused" and row.version == 2
        event = session.scalar(select(Audit).where(Audit.target == schedule_id))
        assert event.details["reason"] == (
            "SOURCE_DENIED" if denial == "revoked" else "LIMIT_REACHED"
        )


def test_success_reserves_once_and_claim_flags_do_not_bypass_source_revalidation(admission_system):
    db, settings, _, _, _ = admission_system
    assert (
        db.dispatch_due_schedules(
            admission=lambda session, row: admit_schedule(session, row, settings)
        )
        == 1
    )
    expected = {"jobs": 1, "runtime_seconds": 60, "pages": 2, "source_bytes": 100_000}
    before = budgets(db)
    for scope in (GLOBAL, "alpha"):
        assert before[(scope, now().strftime("%Y-%m"))] == expected
        assert before[(scope, "total")] == {"metadata_bytes": 16_384}
    claim = db.claim("worker", lease_seconds=600, workspace="alpha")
    assert claim["payload"]["beta_jobs_reserved"] and claim["payload"]["beta_budget_reserved"]
    guard_claim(db, claim, settings)
    guard_claim(db, claim, settings)
    assert budgets(db) == before
    with db.sessions.begin() as session:
        session.get(ApprovedDomain, "example.com").enabled = False
    with pytest.raises(PipelineError, match="approval"):
        guard_claim(db, claim, settings)
    assert budgets(db) == before


def test_unexpected_later_schedule_error_rolls_back_previously_released_savepoint(admission_system):
    db, settings, source, recipe, schedule_id = admission_system
    second_id = seed_schedule(db, "beta", source, recipe)

    def admit(session, row):
        if row.workspace == "beta":
            raise RuntimeError("unexpected admission failure")
        return admit_schedule(session, row, settings)

    with pytest.raises(RuntimeError, match="unexpected"):
        db.dispatch_due_schedules(admission=admit)
    assert not budgets(db) and queued_rows(db) == ([], [])
    with db.sessions() as session:
        for key in (schedule_id, second_id):
            row = session.get(Schedule, key)
            assert row.state == "active" and row.version == 1 and row.last_run_id is None
        assert not list(session.scalars(select(Audit)))


def test_manual_public_resume_remains_etag_fenced_after_admission_pause(admission_system):
    db, settings, source, recipe, _ = admission_system
    raw = LocalStore(Path(settings.artifact_root))
    app = create_app(settings, db, raw, verifier=lambda token: {"sub": "alice"})
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer alice"
        workspace = client.get("/v1/me").json()["workspace"]
        schedule_id = seed_schedule(db, workspace, source, recipe)
        with db.sessions.begin() as session:
            row = session.get(BudgetUsage, (workspace, now().strftime("%Y-%m")))
            row.counters = {**row.counters, "jobs": settings.beta_max_jobs}
        assert (
            db.dispatch_due_schedules(
                workspace=workspace,
                admission=lambda session, row: admit_schedule(session, row, settings),
            )
            == 0
        )
        paused = client.get(f"/v1/schedules/{schedule_id}")
        assert paused.json()["state"] == "paused" and paused.headers["etag"] == '"2"'
        stale = client.post(
            f"/v1/schedules/{schedule_id}/resume",
            json={},
            headers={"If-Match": '"1"', "Idempotency-Key": str(uuid4())},
        )
        assert stale.status_code == 412
        resumed = client.post(
            f"/v1/schedules/{schedule_id}/resume",
            json={},
            headers={"If-Match": paused.headers["etag"], "Idempotency-Key": str(uuid4())},
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["state"] == "active" and resumed.json()["version"] == 3
        assert (
            db.dispatch_due_schedules(
                workspace=workspace,
                admission=lambda session, row: admit_schedule(session, row, settings),
            )
            == 0
        )
        assert queued_rows(db) == ([], [])
