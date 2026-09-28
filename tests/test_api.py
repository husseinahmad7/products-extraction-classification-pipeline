from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from product_pipeline.acquisition import PageCapture
from product_pipeline.contracts import Navigation
from product_pipeline.errors import PipelineError
from product_pipeline.server.api import create_app
from product_pipeline.server.artifacts import QuotaStore
from product_pipeline.server.auth import issue_token
from product_pipeline.server.db import (
    Database,
    DatasetHead,
    Job,
    OriginThrottle,
    Schedule,
    StorageQuota,
    current_job,
    database_now,
    now,
)
from product_pipeline.server.settings import Settings
from product_pipeline.server.worker import Worker
from product_pipeline.storage import LocalStore


@pytest.fixture
def system(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'test.db'}")
    db.initialize()
    store = QuotaStore(LocalStore(tmp_path / "objects"), db, "default", 2_000_000_000)
    with db.sessions.begin() as session:
        token = issue_token(session, "default", "bootstrap")
    client = TestClient(create_app(Settings(), db, store))
    client.headers["Authorization"] = f"Bearer {token}"
    yield client, Worker(db, store), db, store
    client.close()
    db.engine.dispose()


def post(client, path, data=None, **headers):
    return client.post(path, json=data or {}, headers={"Idempotency-Key": str(uuid4()), **headers})


def prepare(system, source, recipe, snapshot):
    client, worker, db, store = system
    assert post(client, "/v1/sources", source.model_dump()).status_code == 201
    key = store.put(snapshot)
    response = post(client, "/v1/recipes", {"spec": recipe.model_dump(), "snapshot_hash": key})
    assert response.status_code == 202, response.text
    worker.once()
    operation = client.get(response.json()["location"]).json()
    assert operation["state"] == "succeeded", operation
    recipe_id = operation["result"]["id"]
    resource = client.get(f"/v1/recipes/{recipe_id}")
    response = post(
        client,
        f"/v1/recipes/{recipe_id}/activate",
        {"decision": "approve", "reason": "Fixture evidence verified"},
        **{"If-Match": resource.headers["etag"]},
    )
    assert response.status_code == 200, response.text
    return recipe_id, key


def test_auth_and_idempotency(system, source):
    client, _, _, _ = system
    assert client.get("/v1/sources", headers={"Authorization": ""}).status_code == 401
    headers = {"Idempotency-Key": "repeat"}
    first = client.post("/v1/sources", json=source.model_dump(), headers=headers)
    second = client.post("/v1/sources", json=source.model_dump(), headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    changed = source.model_dump()
    changed["name"] = "Changed"
    assert client.post("/v1/sources", json=changed, headers=headers).status_code == 409


def test_complete_offline_lifecycle(system, source, recipe, snapshot):
    client, worker, db, store = system
    recipe_id, key = prepare(system, source, recipe, snapshot)
    response = post(client, "/v1/runs", {"recipe_id": recipe_id, "snapshot_hash": key})
    assert response.status_code == 202
    worker.once()
    run_id = response.json()["run_id"]
    run = client.get(f"/v1/runs/{run_id}").json()["data"]
    assert run["state"] == "succeeded", run
    assert run["counts"] == {"records": 2, "quarantined": 0}
    records = client.get(f"/v1/runs/{run_id}/records").json()
    assert records["total"] == 2 and records["items"][0]["evidence"]
    exported = client.get(f"/v1/revisions/{run['revision_id']}/export")
    assert len(exported.text.splitlines()) == 2
    replay = post(client, f"/v1/runs/{run_id}/replay")
    worker.once()
    assert client.get(replay.json()["location"]).json()["state"] == "succeeded"
    actions = [a["action"] for a in client.get("/v1/audit").json()["items"]]
    assert "dataset.published" in actions and "run.replayed" in actions


def test_paginated_live_run_retains_and_replays_each_page(system, source, recipe, snapshot):
    client, worker, _, _ = system
    source = source.model_copy(update={"navigation": Navigation(kind="json_next", value="/next")})
    recipe_id, _ = prepare(system, source, recipe, snapshot)
    first = b'{"items":[{"sku":"A","name":"Drill"}],"next":"/page-2"}'
    second = b'{"items":[{"sku":"B","name":"Saw"}]}'

    def pages(spec, *, retained, reserve):
        assert not retained
        yield PageCapture(spec.url, first, first, request_url=spec.url)
        yield PageCapture(
            "https://example.com/page-2", second, second, request_url="https://example.com/page-2"
        )

    response = post(client, "/v1/runs", {"recipe_id": recipe_id})
    with patch("product_pipeline.server.worker.acquire_pages", side_effect=pages):
        assert worker.once()
    run_id = response.json()["run_id"]
    run = client.get(f"/v1/runs/{run_id}").json()["data"]
    assert run["state"] == "succeeded" and len(run["pages"]) == 2
    assert run["counts"] == {"records": 2, "quarantined": 0}
    records = client.get(f"/v1/runs/{run_id}/records").json()["items"]
    assert {row["snapshot_hash"] for row in records} == {
        page["snapshot_hash"] for page in run["pages"]
    }
    replay = post(client, f"/v1/runs/{run_id}/replay")
    assert worker.once()
    assert client.get(replay.json()["location"]).json()["state"] == "succeeded"


def test_paginated_retry_reuses_checkpointed_page(system, source, recipe, snapshot):
    client, worker, db, _ = system
    source = source.model_copy(update={"navigation": Navigation(kind="json_next", value="/next")})
    recipe_id, _ = prepare(system, source, recipe, snapshot)
    first = b'{"items":[{"sku":"A","name":"Drill"}],"next":"/page-2"}'
    second = b'{"items":[{"sku":"B","name":"Saw"}]}'
    retained_counts = []

    def pages(spec, *, retained, reserve):
        retained_counts.append(len(retained))
        if retained:
            yield retained[0]
            yield PageCapture(
                "https://example.com/page-2",
                second,
                second,
                request_url="https://example.com/page-2",
            )
        else:
            yield PageCapture(spec.url, first, first, request_url=spec.url)
            raise PipelineError("FETCH_FAILED", "temporary acquisition failure", retryable=True)

    response = post(client, "/v1/runs", {"recipe_id": recipe_id})
    with patch("product_pipeline.server.worker.acquire_pages", side_effect=pages):
        assert worker.once()
        run = client.get(f"/v1/runs/{response.json()['run_id']}").json()["data"]
        assert run["state"] == "queued" and len(run["pages"]) == 1
        with db.sessions.begin() as session:
            session.get(Job, response.json()["id"]).available = now()
        assert worker.once()
    run = client.get(f"/v1/runs/{response.json()['run_id']}").json()["data"]
    assert retained_counts == [0, 1]
    assert run["state"] == "succeeded" and len(run["pages"]) == 2


def test_schedule_dispatch_is_atomic_and_coalesces_missed_intervals(
    system, source, recipe, snapshot
):
    client, _, db, _ = system
    recipe_id, _ = prepare(system, source, recipe, snapshot)
    created = post(client, "/v1/schedules", {"recipe_id": recipe_id, "interval_seconds": 60})
    assert created.status_code == 201, created.text
    schedule_id = created.json()["id"]
    with db.sessions.begin() as session:
        session.get(Schedule, schedule_id).next_due = now() - timedelta(days=1)
    assert db.dispatch_due_schedules(workspace="default") == 1
    assert db.dispatch_due_schedules(workspace="default") == 0
    schedule = client.get(f"/v1/schedules/{schedule_id}")
    assert schedule.json()["last_run_id"]
    operations = client.get("/v1/operations?limit=1").json()
    assert len(operations["items"]) == 1 and operations["next_cursor"]
    assert operations["items"][0]["kind"] == "run"
    assert client.get(f"/v1/operations?limit=1&cursor={operations['next_cursor']}").json()["items"]
    paused = post(
        client, f"/v1/schedules/{schedule_id}/pause", **{"If-Match": schedule.headers["etag"]}
    )
    assert paused.status_code == 200 and paused.json()["state"] == "paused"
    assert db.dispatch_due_schedules(workspace="default") == 0
    refreshed = client.get(f"/v1/schedules/{schedule_id}")
    resumed = post(
        client, f"/v1/schedules/{schedule_id}/resume", **{"If-Match": refreshed.headers["etag"]}
    )
    assert resumed.status_code == 200 and resumed.json()["state"] == "active"


def test_origin_slots_are_shared_and_bounded(system):
    _, _, db, _ = system
    origin = "https://example.com"
    assert db.reserve_origin(origin, 5, 10) < 0.5
    assert 0 < db.reserve_origin(origin, 5, 10) <= 5
    with db.sessions.begin() as session:
        session.get(OriginThrottle, origin).next_allowed = database_now(session) + timedelta(days=1)
    with pytest.raises(PipelineError, match="deadline"):
        db.reserve_origin(origin, 5, 1)


def test_removals_require_versioned_human_decision(system, source, recipe, snapshot):
    client, worker, db, store = system
    recipe_id, key = prepare(system, source, recipe, snapshot)
    post(client, "/v1/runs", {"recipe_id": recipe_id, "snapshot_hash": key})
    worker.once()
    changed_key = store.put(b'{"items":[{"sku":"A","name":"Cordless drill","price":42}]}')
    response = post(client, "/v1/runs", {"recipe_id": recipe_id, "snapshot_hash": changed_key})
    worker.once()
    run = client.get(f"/v1/runs/{response.json()['run_id']}").json()["data"]
    assert run["state"] == "awaiting_review"
    review_id = run["review_id"]
    decision = {"decision": "approve", "reason": "Expected catalog discontinuation"}
    assert (
        post(
            client, f"/v1/reviews/{review_id}/decision", decision, **{"If-Match": '"99"'}
        ).status_code
        == 412
    )
    review = client.get(f"/v1/reviews/{review_id}")
    assert (
        post(
            client,
            f"/v1/reviews/{review_id}/decision",
            decision,
            **{"If-Match": review.headers["etag"]},
        ).status_code
        == 200
    )
    assert (
        client.get(f"/v1/runs/{response.json()['run_id']}").json()["data"]["state"] == "succeeded"
    )


def test_cancel_rejects_current_worker_commit(system, source, recipe, snapshot):
    client, worker, db, store = system
    recipe_id, key = prepare(system, source, recipe, snapshot)
    response = post(client, "/v1/runs", {"recipe_id": recipe_id, "snapshot_hash": key})
    claim = db.claim("old-worker")
    assert (
        post(client, f"/v1/operations/{response.json()['id']}/cancel").json()["state"]
        == "cancelled"
    )
    with db.sessions.begin() as session, pytest.raises(PipelineError, match="generation"):
        current_job(session, claim)
    assert not db.heartbeat(claim)


def test_expired_generation_is_fenced(system):
    _, _, db, _ = system
    with db.sessions.begin() as session:
        session.add(Job(id="job", workspace="default", kind="run", payload={}))
    old = db.claim("old")
    with db.sessions.begin() as session:
        session.get(Job, "job").lease_until = now() - timedelta(seconds=1)
    new = db.claim("new")
    assert new["generation"] == old["generation"] + 1
    with db.sessions.begin() as session:
        with pytest.raises(PipelineError):
            current_job(session, old)
        assert current_job(session, new).owner == "new"


def test_invalid_recipe_cannot_activate(system, source, recipe):
    client, worker, _, store = system
    post(client, "/v1/sources", source.model_dump())
    key = store.put(b'{"items":[{"sku":"A"}]}')
    response = post(client, "/v1/recipes", {"spec": recipe.model_dump(), "snapshot_hash": key})
    worker.once()
    recipe_id = client.get(response.json()["location"]).json()["result"]["id"]
    row = client.get(f"/v1/recipes/{recipe_id}")
    assert row.json()["data"]["state"] == "invalid"
    assert (
        post(
            client,
            f"/v1/recipes/{recipe_id}/activate",
            {"decision": "approve", "reason": "Try to bypass validation"},
            **{"If-Match": row.headers["etag"]},
        ).status_code
        == 422
    )


def test_upload_is_bounded_and_text_only(system):
    client, _, _, _ = system
    assert (
        client.put(
            "/v1/artifacts",
            content=b"weights",
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == 422
    )
    response = client.put(
        "/v1/artifacts", content=b'{"x":1}', headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 200 and len(response.json()["hash"]) == 64


def test_request_limit_precedes_json_parsing(system):
    client, _, _, _ = system
    response = client.post(
        "/v1/sources", content=b"x" * 1_000_001, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["title"] == "REQUEST_TOO_LARGE"


def test_security_headers_are_present(system):
    client, _, _, _ = system
    response = client.get("/v1/sources")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in response.headers["cache-control"]


def test_token_rotation_revokes_old_token(system):
    client, _, _, _ = system
    response = client.post("/v1/admin-tokens/rotate", json={"overlap_seconds": 0})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert client.get("/v1/sources").status_code == 401
    client.headers["Authorization"] = "Bearer " + response.json()["token"]
    assert client.get("/v1/sources").status_code == 200


def test_quota_pause_resume_reuses_the_retained_capture(system, source, recipe, snapshot):
    from product_pipeline.acquisition import normalize

    client, worker, db, store = system
    recipe_id, _ = prepare(system, source, recipe, snapshot)
    raw = b'{ "items": [{"sku":"C","name":"New drill","price":50}] }\r\n'
    captures = []

    def acquire_once(_source):
        captures.append(1)
        assert len(captures) == 1, "a paused capture must not be fetched again"
        return raw, normalize(raw, "json")

    worker.acquire = acquire_once
    with db.sessions.begin() as session:
        quota = session.get(StorageQuota, "default")
        quota.limit_bytes = quota.used_bytes + len(raw)
    response = post(client, "/v1/runs", {"recipe_id": recipe_id})
    operation_id, run_id = response.json()["id"], response.json()["run_id"]
    assert worker.once()
    paused = client.get(f"/v1/operations/{operation_id}").json()
    assert paused["state"] == "paused" and paused["attempts"] == 0
    assert not worker.once(), "paused jobs must not hot-loop"
    run = client.get(f"/v1/runs/{run_id}").json()["data"]
    assert run["state"] == "paused" and run["raw_hash"]
    assert store.get(run["raw_hash"]) == raw
    assert post(client, f"/v1/operations/{operation_id}/resume").status_code == 507
    assert (
        post(
            client,
            "/v1/storage/expand",
            {
                "limit_bytes": 2_000_000,
                "reason": "Verified backing disk has sufficient capacity",
            },
        ).status_code
        == 200
    )
    assert post(client, f"/v1/operations/{operation_id}/resume").status_code == 202
    assert worker.once()
    assert len(captures) == 1
    assert client.get(f"/v1/runs/{run_id}").json()["data"]["state"] == "succeeded"
    assert client.get(f"/v1/operations/{operation_id}").json()["generation"] == 2
    actions = [a["action"] for a in client.get("/v1/audit").json()["items"]]
    assert {"job.paused", "storage.expanded", "operation.resumed"} <= set(actions)


def test_quota_cannot_shrink_or_resume_nonpaused_job(system):
    client, _, _, _ = system
    quota = client.get("/v1/storage").json()
    assert quota == {"limit_bytes": 2_000_000_000, "used_bytes": 0}
    assert (
        post(
            client,
            "/v1/storage/expand",
            {
                "limit_bytes": 10,
                "reason": "Attempted shrink is not allowed",
            },
        ).status_code
        == 409
    )
    assert post(client, "/v1/operations/unknown/resume").status_code == 404


def test_publication_conflict_preserves_result_evidence(
    system, source, recipe, snapshot, monkeypatch
):
    client, worker, db, store = system
    recipe_id, key = prepare(system, source, recipe, snapshot)
    original_put = store.put

    def advance_head_after_result(data):
        result_key = original_put(data)
        with db.sessions.begin() as session:
            head = session.get(DatasetHead, ("default", source.id))
            head.version += 1
        return result_key

    monkeypatch.setattr(store, "put", advance_head_after_result)
    response = post(client, "/v1/runs", {"recipe_id": recipe_id, "snapshot_hash": key})
    assert worker.once()
    run_id = response.json()["run_id"]
    run = client.get(f"/v1/runs/{run_id}").json()["data"]
    assert run["state"] == "failed"
    assert run["error"]["title"] == "PUBLICATION_CONFLICT"
    assert run["result_key"]
    assert client.get(f"/v1/runs/{run_id}/records").json()["total"] == 2
