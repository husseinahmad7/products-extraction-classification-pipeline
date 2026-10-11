"""Bounded dispatch uses no idle sleeps and preserves fenced evidence."""

import json
from datetime import timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from product_pipeline.acquisition import PageCapture
from product_pipeline.cli import main
from product_pipeline.contracts import Navigation
from product_pipeline.errors import PipelineError
from product_pipeline.server.artifacts import QuotaStore
from product_pipeline.server.db import Audit, Database, DatasetHead, Job, Resource, now
from product_pipeline.server.worker import Worker
from product_pipeline.storage import LocalStore


@pytest.fixture
def batch_system(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'worker.db'}")
    db.initialize()
    raw = LocalStore(tmp_path / "objects")

    def factory(workspace):
        return QuotaStore(raw, db, workspace, 2_000_000)

    yield db, factory
    db.engine.dispose()


def enqueue_run(db, factory, workspace, source, recipe, snapshot=None):
    key = factory(workspace).put(snapshot) if snapshot is not None else None
    run_id = f"run-{workspace}"
    with db.sessions.begin() as session:
        session.add(
            Resource(workspace=workspace, kind="source", id=source.id, data=source.model_dump())
        )
        session.add(
            Resource(
                workspace=workspace, kind="recipe", id="recipe", data={"spec": recipe.model_dump()}
            )
        )
        session.add(DatasetHead(workspace=workspace, source_id=source.id))
        session.add(
            Resource(
                workspace=workspace,
                kind="run",
                id=run_id,
                data={
                    "source_id": source.id,
                    "recipe_id": "recipe",
                    "state": "queued",
                    **({"snapshot_hash": key} if key else {}),
                },
            )
        )
        session.add(
            Job(id=f"job-{workspace}", workspace=workspace, kind="run", payload={"run_id": run_id})
        )
    return run_id


def test_batch_exits_on_first_idle_without_sleep(batch_system, monkeypatch):
    db, factory = batch_system
    dispatch = Mock(wraps=db.dispatch_due_schedules)
    claim = Mock(wraps=db.claim)
    monkeypatch.setattr(db, "dispatch_due_schedules", dispatch)
    monkeypatch.setattr(db, "claim", claim)
    monkeypatch.setattr(
        "product_pipeline.server.worker.time.sleep", Mock(side_effect=AssertionError)
    )
    report = Worker(db, store_factory=factory).run_batch()
    assert report.processed == 0 and report.reason == "idle"
    assert dispatch.call_count == claim.call_count == 1


def test_factory_batch_claims_each_tenant_and_enforces_scoped_reads(
    batch_system, source, recipe, snapshot
):
    db, factory = batch_system
    for workspace in ("alpha", "beta"):
        enqueue_run(db, factory, workspace, source, recipe, snapshot)
    claimed = []
    report = Worker(
        db, store_factory=factory, claim_guard=lambda claim: claimed.append(claim["workspace"])
    ).run_batch()
    assert report.processed == 2 and report.reason == "idle"
    assert set(claimed) == {"alpha", "beta"}
    with db.sessions() as session:
        alpha = session.get(Resource, ("alpha", "run", "run-alpha"))
        beta = session.get(Resource, ("beta", "run", "run-beta"))
        assert alpha.data["state"] == beta.data["state"] == "succeeded"
        assert alpha.data["result_key"] != beta.data["result_key"]
        with pytest.raises(PipelineError, match="this workspace"):
            factory("beta").get(alpha.data["result_key"])


def test_scoped_worker_cannot_select_another_tenant(batch_system, source, recipe, snapshot):
    db, factory = batch_system
    enqueue_run(db, factory, "beta", source, recipe, snapshot)
    worker = Worker(db, factory("alpha"))
    assert worker.run_batch().processed == 0
    with pytest.raises(ValueError, match="another workspace"):
        worker.once("beta")
    with pytest.raises(ValueError, match="quota store"):
        Worker(db, factory("alpha"), workspace="beta")


def test_batch_respects_job_limit(batch_system, source, recipe, snapshot):
    db, factory = batch_system
    for workspace in ("alpha", "beta"):
        enqueue_run(db, factory, workspace, source, recipe, snapshot)
    report = Worker(db, store_factory=factory).run_batch(max_jobs=1)
    assert report.processed == 1 and report.reason == "job_limit"
    with db.sessions() as session:
        assert len(list(session.scalars(select(Job).where(Job.state == "queued")))) == 1


@pytest.mark.parametrize(
    "jobs,seconds", [(0, 120), (1, 0), (1, -1), (1, float("nan")), (1, float("inf"))]
)
def test_invalid_budgets_fail_before_query(batch_system, jobs, seconds):
    db, factory = batch_system
    with pytest.raises(ValueError, match="positive"):
        Worker(db, store_factory=factory).run_batch(jobs, seconds)


def test_deadline_checkpoint_is_reused_without_consuming_retry(
    batch_system, source, recipe, monkeypatch
):
    db, factory = batch_system
    source = source.model_copy(update={"navigation": Navigation(kind="json_next", value="/next")})
    run_id = enqueue_run(db, factory, "alpha", source, recipe)
    first = b'{"items":[{"sku":"A","name":"Drill"}],"next":"/page-2"}'
    second = b'{"items":[{"sku":"B","name":"Saw"}]}'
    clock = [0.0]
    retained_counts = []
    time_limits = []
    monkeypatch.setattr("product_pipeline.server.worker.time.monotonic", lambda: clock[0])

    def pages(spec, *, retained, reserve):
        retained_counts.append(len(retained))
        time_limits.append(spec.limits.deadline_seconds)
        if not retained:
            clock[0] = 121
            yield PageCapture(spec.url, first, first, request_url=spec.url)
            raise AssertionError("expired batch must not acquire a second page")
        yield retained[0]
        yield PageCapture(
            "https://example.com/page-2", second, second, request_url="https://example.com/page-2"
        )

    monkeypatch.setattr("product_pipeline.server.worker.acquire_pages", pages)
    worker = Worker(db, store_factory=factory)
    report = worker.run_batch()
    assert report.reason == "deadline" and report.processed == 1
    with db.sessions.begin() as session:
        job = session.get(Job, "job-alpha")
        assert job.state == "queued" and job.attempts == 0
        assert job.owner is None and job.error["title"] == "BATCH_DEADLINE"
        run = session.get(Resource, ("alpha", "run", run_id))
        assert len(run.data["pages"]) == 1 and not run.data.get("pages_complete")
        assert factory("alpha").get(run.data["pages"][0]["raw_hash"]) == first
        job.available = now()
    clock[0] = 200
    assert worker.run_batch().processed == 1
    with db.sessions() as session:
        run = session.get(Resource, ("alpha", "run", run_id))
        assert run.data["state"] == "succeeded" and len(run.data["pages"]) == 2
        assert "job.yielded" in list(session.scalars(select(Audit.action)))
    assert retained_counts == [0, 1]
    assert time_limits == [120, 120]


def test_claim_guard_pauses_before_acquisition(batch_system, source, recipe):
    db, factory = batch_system
    enqueue_run(db, factory, "alpha", source, recipe)
    acquire = Mock(side_effect=AssertionError("budget denied work must not fetch"))

    def guard(claim):
        raise PipelineError("QUOTA_EXCEEDED", "public budget reserved")

    assert (
        Worker(db, store_factory=factory, claim_guard=guard, acquire_fn=acquire)
        .run_batch()
        .processed
        == 1
    )
    acquire.assert_not_called()
    with db.sessions() as session:
        job = session.get(Job, "job-alpha")
        assert job.state == "paused" and job.attempts == 0


def test_factory_cannot_return_another_workspace_store(batch_system, source, recipe):
    db, factory = batch_system
    enqueue_run(db, factory, "alpha", source, recipe)
    acquire = Mock(side_effect=AssertionError("mismatched tenant store must not fetch"))
    assert (
        Worker(db, store_factory=lambda workspace: factory("beta"), acquire_fn=acquire)
        .run_batch()
        .processed
        == 1
    )
    acquire.assert_not_called()
    with db.sessions() as session:
        job = session.get(Job, "job-alpha")
        assert job.state == "failed" and job.error["title"] == "STATE_CORRUPT"


def test_source_deadline_is_not_mistaken_for_a_batch_yield(batch_system, source, recipe):
    db, factory = batch_system
    source = source.model_copy(
        update={"limits": source.limits.model_copy(update={"deadline_seconds": 1})}
    )
    enqueue_run(db, factory, "alpha", source, recipe)
    acquire = Mock(side_effect=PipelineError("LIMIT_REACHED", "acquisition deadline reached"))
    assert Worker(db, store_factory=factory, acquire_fn=acquire).run_batch().processed == 1
    with db.sessions() as session:
        job = session.get(Job, "job-alpha")
        assert job.state == "failed" and job.error["title"] == "LIMIT_REACHED"
        assert job.attempts == 1


def test_expired_host_lease_reuses_retained_capture(batch_system, source, recipe, monkeypatch):
    db, factory = batch_system
    source = source.model_copy(update={"navigation": Navigation(kind="json_next", value="/next")})
    run_id = enqueue_run(db, factory, "alpha", source, recipe)
    first = b'{"items":[{"sku":"A","name":"Drill"}],"next":"/page-2"}'
    second = b'{"items":[{"sku":"B","name":"Saw"}]}'
    key = factory("alpha").put(first)
    with db.sessions.begin() as session:
        run = session.get(Resource, ("alpha", "run", run_id))
        run.data = {
            **run.data,
            "state": "running",
            "pages": [
                {
                    "url": source.url,
                    "request_url": source.url,
                    "raw_hash": key,
                    "snapshot_hash": key,
                }
            ],
        }
        job = session.get(Job, "job-alpha")
        job.state, job.owner = "running", "evicted-host"
        job.generation, job.attempts = 1, 1
        job.lease_until = now() - timedelta(seconds=1)

    def pages(spec, *, retained, reserve):
        assert len(retained) == 1 and retained[0].raw == first
        yield retained[0]
        yield PageCapture(
            "https://example.com/page-2", second, second, request_url="https://example.com/page-2"
        )

    monkeypatch.setattr("product_pipeline.server.worker.acquire_pages", pages)
    assert Worker(db, store_factory=factory).run_batch(max_seconds=600).processed == 1
    with db.sessions() as session:
        run = session.get(Resource, ("alpha", "run", run_id))
        job = session.get(Job, "job-alpha")
        assert run.data["state"] == "succeeded" and len(run.data["pages"]) == 2
        assert job.generation == job.attempts == 2


def test_cli_defaults_to_idle_exiting_batch(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PIPELINE_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("PIPELINE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("PIPELINE_AUTH_MODE", "admin")
    assert main(["init-db"]) == 0
    capsys.readouterr()
    assert main(["worker"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["processed"] == 0 and report["reason"] == "idle"


def test_cli_invalid_budget_creates_no_database(tmp_path, monkeypatch, capsys):
    path = tmp_path / "not-created.db"
    monkeypatch.setenv("PIPELINE_DATABASE_URL", f"sqlite:///{path}")
    assert main(["worker", "--max-jobs", "0"]) == 2
    assert "positive" in capsys.readouterr().err and not path.exists()


def test_public_cli_rejects_forever_without_opening_database(tmp_path, monkeypatch, capsys):
    path = tmp_path / "not-created.db"
    monkeypatch.setenv("PIPELINE_DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("PIPELINE_AUTH_MODE", "neon")
    monkeypatch.setenv("NEON_AUTH_BASE_URL", "https://auth.invalid")
    monkeypatch.setenv("NEON_AUTH_JWKS_URL", "https://auth.invalid/.well-known/jwks.json")
    assert main(["worker", "--forever"]) == 2
    assert "WORKER_MODE_DENIED" in capsys.readouterr().err and not path.exists()
