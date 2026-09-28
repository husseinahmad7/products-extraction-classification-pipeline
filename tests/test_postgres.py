"""Run only against the explicitly configured disposable PostgreSQL test database."""

import multiprocessing
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError

from product_pipeline.errors import PipelineError
from product_pipeline.server.artifacts import QuotaStore
from product_pipeline.server.db import (
    Audit,
    Database,
    Job,
    OriginThrottle,
    Resource,
    Schedule,
    StorageQuota,
    audit,
    current_job,
    now,
)
from product_pipeline.storage import LocalStore

pytestmark = pytest.mark.skipif(
    not os.getenv("PIPELINE_TEST_DATABASE_URL"),
    reason="disposable PostgreSQL database not configured",
)


def _claim_in_separate_process(database_url, kind, connection):
    db = Database(database_url)
    try:
        connection.send(db.claim("doomed-process", lease_seconds=2, kinds=(kind,)))
        connection.recv()
    finally:
        connection.close()
        db.engine.dispose()


def test_postgres_recovers_lease_after_process_death():
    """Kill an actual lease holder, then reject its stale generation on recovery."""
    database_url = os.environ["PIPELINE_TEST_DATABASE_URL"]
    db = Database(database_url)
    kind = "test-" + uuid4().hex
    with db.sessions.begin() as session:
        session.add(Job(workspace="test", kind=kind, payload={}))
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    worker = context.Process(target=_claim_in_separate_process, args=(database_url, kind, child))
    worker.start()
    child.close()
    try:
        assert parent.poll(30), "lease holder did not start"
        old = parent.recv()
        assert old is not None
        assert db.heartbeat(old, lease_seconds=1)
        worker.terminate()
        worker.join(timeout=10)
        assert not worker.is_alive()
        deadline = time.monotonic() + 10
        current = None
        while current is None and time.monotonic() < deadline:
            current = db.claim("recovery-process", kinds=(kind,))
            if current is None:
                time.sleep(0.05)
        assert current is not None
        assert current["id"] == old["id"]
        assert current["generation"] == old["generation"] + 1
        assert not db.heartbeat(old)
        with pytest.raises(PipelineError, match="generation"), db.sessions.begin() as session:
            current_job(session, old)
        assert db.heartbeat(current)
    finally:
        parent.close()
        if worker.is_alive():
            worker.terminate()
        worker.join(timeout=10)
        db.engine.dispose()


def test_skip_locked_and_append_only_audit():
    db = Database(os.environ["PIPELINE_TEST_DATABASE_URL"])
    kind = "test-" + str(uuid4())[:30]
    keys = [str(uuid4()), str(uuid4())]
    with db.sessions.begin() as session:
        for key in keys:
            session.add(Job(id=key, workspace="test", kind=kind, payload={}))
        audit(session, "test", "integration-test", "probe", kind)
    with db.sessions.begin() as locked:
        first = locked.scalar(
            select(Job)
            .where(Job.kind == kind)
            .order_by(Job.available, Job.id)
            .with_for_update()
            .limit(1)
        )
        second = db.claim("other-worker", kinds=(kind,))
        assert second and second["id"] != first.id
    with pytest.raises(DBAPIError, match="append-only"), db.sessions.begin() as session:
        session.execute(update(Audit).where(Audit.target == kind).values(action="tampered"))
    db.engine.dispose()


def test_expired_postgres_generation_cannot_commit():
    db = Database(os.environ["PIPELINE_TEST_DATABASE_URL"])
    kind = "test-" + uuid4().hex
    with db.sessions.begin() as session:
        session.add(Job(workspace="test", kind=kind, payload={}))
    old = db.claim("expired-worker", kinds=(kind,))
    with db.sessions.begin() as session:
        session.get(Job, old["id"]).lease_until = now() - timedelta(seconds=1)
    current = db.claim("current-worker", kinds=(kind,))
    assert current["generation"] == old["generation"] + 1
    assert not db.heartbeat(old)
    with pytest.raises(PipelineError, match="generation"), db.sessions.begin() as session:
        current_job(session, old)
    assert db.heartbeat(current)
    db.engine.dispose()


def test_postgres_quota_does_not_oversubscribe(tmp_path):
    db = Database(os.environ["PIPELINE_TEST_DATABASE_URL"])
    workspace = "quota-" + uuid4().hex
    store = QuotaStore(LocalStore(tmp_path), db, workspace, 10)

    def retain(i):
        try:
            store.put(f"obj{i}".encode())
            return True
        except PipelineError as exc:
            assert exc.code == "QUOTA_EXCEEDED"
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(retain, range(8)))
    assert sum(outcomes) == 2
    with db.sessions() as session:
        assert session.get(StorageQuota, workspace).used_bytes == 8
    db.engine.dispose()


def test_postgres_origin_slots_serialize_across_connections():
    database_url = os.environ["PIPELINE_TEST_DATABASE_URL"]
    origin = "https://" + uuid4().hex + ".example"
    barrier = threading.Barrier(2)

    def reserve():
        db = Database(database_url)
        try:
            barrier.wait(timeout=10)
            return db.reserve_origin(origin, 5, 30)
        finally:
            db.engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as executor:
        waits = sorted(executor.map(lambda _: reserve(), range(2)))
    assert waits[0] < 1
    assert waits[1] > 3
    db = Database(database_url)
    with db.sessions() as session:
        assert session.get(OriginThrottle, origin) is not None
    db.engine.dispose()


def test_postgres_schedule_dispatch_is_single_transaction_under_race():
    db = Database(os.environ["PIPELINE_TEST_DATABASE_URL"])
    workspace = "schedule-" + uuid4().hex
    source_id, recipe_id = "source", "recipe"
    with db.sessions.begin() as session:
        session.add(
            Resource(
                workspace=workspace,
                kind="active_recipe",
                id=source_id,
                data={"recipe_id": recipe_id},
            )
        )
        session.add(
            Schedule(
                workspace=workspace,
                source_id=source_id,
                recipe_id=recipe_id,
                interval_seconds=60,
                next_due=now() - timedelta(seconds=1),
                state="active",
                version=1,
            )
        )
    barrier = threading.Barrier(2)

    def dispatch():
        barrier.wait(timeout=10)
        return db.dispatch_due_schedules(workspace=workspace)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(lambda _: dispatch(), range(2)))
    assert outcomes == [0, 1]
    with db.sessions() as session:
        jobs = session.scalars(
            select(Job).where(Job.workspace == workspace, Job.kind == "run")
        ).all()
        assert len(jobs) == 1
        run = session.get(Resource, (workspace, "run", jobs[0].payload["run_id"]))
        assert run is not None and run.data["schedule_id"]
    db.engine.dispose()


@pytest.mark.parametrize("kind", ["schema", "taxonomy", "revision"])
def test_postgres_immutable_resources_reject_changes(kind):
    db = Database(os.environ["PIPELINE_TEST_DATABASE_URL"])
    key = str(uuid4())
    with db.sessions.begin() as session:
        session.add(Resource(workspace="test", kind=kind, id=key, data={}))
    for statement in (
        update(Resource).where(Resource.id == key).values(data={"tampered": True}),
        delete(Resource).where(Resource.id == key),
    ):
        with pytest.raises(DBAPIError, match="immutable resource"), db.sessions.begin() as session:
            session.execute(statement)
    db.engine.dispose()
