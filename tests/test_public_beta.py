from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from product_pipeline.contracts import Limits
from product_pipeline.hashing import digest, object_hash
from product_pipeline.server.api import create_app
from product_pipeline.server.db import ApprovedDomain, Artifact, BudgetUsage, Database, StorageQuota
from product_pipeline.server.public_beta import guard_claim
from product_pipeline.server.settings import Settings
from product_pipeline.server.worker import Worker
from product_pipeline.storage import LocalStore


@pytest.fixture
def beta(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'beta.db'}")
    db.initialize()
    settings = Settings(
        auth_mode="neon",
        neon_auth_issuer="https://auth.example.com/auth",
        neon_auth_jwks_url="https://auth.example.com/auth/.well-known/jwks.json",
        operator_subjects=("operator",),
    )
    raw = LocalStore(tmp_path / "objects")

    def verify(token):
        from product_pipeline.errors import PipelineError

        if token not in {"alice", "bob", "operator"}:
            raise PipelineError("UNAUTHORIZED", "invalid test token")
        return {"sub": token, "workspace": "forged-shared", "role": "admin"}

    app = create_app(settings, db, raw, verifier=verify)
    client = TestClient(app)
    worker = Worker(
        db,
        store_factory=lambda tenant: settings.store(db, workspace=tenant),
        claim_guard=lambda claim: guard_claim(db, claim, settings),
    )
    # Share the same test object root with per-tenant stores.
    worker.store_factory = lambda tenant: __import__(
        "product_pipeline.server.artifacts", fromlist=["QuotaStore"]
    ).QuotaStore(
        raw,
        db,
        tenant,
        settings.beta_artifact_quota_bytes,
        global_limit_bytes=settings.beta_global_artifact_quota_bytes,
    )
    yield client, db, raw, worker, settings
    client.close()
    db.engine.dispose()


def user(client, name):
    client.headers["Authorization"] = "Bearer " + name
    return client.get("/v1/me").json()


def post(client, path, body=None, **headers):
    return client.post(path, json=body or {}, headers={"Idempotency-Key": str(uuid4()), **headers})


def approve(beta, domain="example.com"):
    client = beta[0]
    user(client, "alice")
    request = post(
        client, "/v1/domain-requests", {"domain": domain, "reason": "Public catalog terms reviewed"}
    )
    assert request.status_code == 201, request.text
    request_id = request.json()["id"]
    assert (
        post(
            client,
            f"/v1/domain-requests/{request_id}/decision",
            {"decision": "approve", "reason": "Self approval denied"},
            **{"If-Match": '"1"'},
        ).status_code
        == 403
    )
    user(client, "operator")
    response = post(
        client,
        f"/v1/domain-requests/{request_id}/decision",
        {"decision": "approve", "reason": "Public terms and robots checked"},
        **{"If-Match": '"1"'},
    )
    assert response.status_code == 200, response.text
    user(client, "alice")


def prepare(beta, source, recipe, snapshot):
    client, _, _, worker, _ = beta
    approve(beta)
    source = source.model_copy(
        update={
            "limits": Limits(
                max_pages=10, max_records=1000, max_bytes=1_000_000, deadline_seconds=60
            )
        }
    )
    assert post(client, "/v1/sources", source.model_dump(mode="json")).status_code == 201
    uploaded = client.put(
        "/v1/artifacts", content=snapshot, headers={"Content-Type": "application/json"}
    )
    assert uploaded.status_code == 200, uploaded.text
    key = uploaded.json()["hash"]
    response = post(
        client, "/v1/recipes", {"spec": recipe.model_dump(mode="json"), "snapshot_hash": key}
    )
    assert response.status_code == 202, response.text
    assert worker.once()
    result = client.get(response.json()["location"]).json()
    assert result["state"] == "succeeded", result
    recipe_id = result["result"]["id"]
    row = client.get(f"/v1/recipes/{recipe_id}")
    assert (
        post(
            client,
            f"/v1/recipes/{recipe_id}/activate",
            {"decision": "approve", "reason": "Fixture evidence verified"},
            **{"If-Match": row.headers["etag"]},
        ).status_code
        == 200
    )
    queued = post(client, "/v1/runs", {"recipe_id": recipe_id, "snapshot_hash": key})
    assert queued.status_code == 202, queued.text
    assert worker.once()
    run = client.get(f"/v1/runs/{queued.json()['run_id']}").json()
    assert run["data"]["state"] == "succeeded", run
    return recipe_id, key, queued.json(), run


def test_workspace_and_operator_not_taken_from_claims(beta):
    client = beta[0]
    alice, bob = user(client, "alice"), user(client, "bob")
    assert alice["workspace"] != bob["workspace"] != "forged-shared"
    assert not alice["is_operator"] and not bob["is_operator"]
    assert user(client, "operator")["is_operator"]
    assert client.get("/v1/me", headers={"Authorization": "Bearer forged"}).status_code == 401


def test_domain_requests_are_private_and_stale_decisions_fail(beta):
    client = beta[0]
    user(client, "alice")
    request_id = post(
        client,
        "/v1/domain-requests",
        {"domain": "example.com", "reason": "Request this public catalog"},
    ).json()["id"]
    user(client, "bob")
    assert client.get("/v1/domain-requests").json()["items"] == []
    assert client.get(f"/v1/domain-requests/{request_id}").status_code == 404
    user(client, "operator")
    assert len(client.get("/v1/domain-requests").json()["items"]) == 1
    assert (
        post(
            client,
            f"/v1/domain-requests/{request_id}/decision",
            {"decision": "approve", "reason": "Reviewed public access"},
            **{"If-Match": '"9"'},
        ).status_code
        == 412
    )


@pytest.mark.parametrize(
    "domain",
    [
        "127.0.0.1",
        "localhost",
        "service.internal",
        "*.example.com",
        "https://example.com",
        "user@example.com",
    ],
)
def test_unsafe_domain_requests_denied(beta, domain):
    client = beta[0]
    user(client, "alice")
    assert post(
        client, "/v1/domain-requests", {"domain": domain, "reason": "Should be rejected"}
    ).status_code in {403, 422}


def test_source_approval_authrefs_and_limits(beta, source):
    client = beta[0]
    user(client, "alice")
    data = source.model_dump(mode="json")
    assert post(client, "/v1/sources", data).status_code == 403
    approve(beta)
    assert post(client, "/v1/sources", data).status_code == 422
    data["limits"].update(max_pages=10, max_records=1000, max_bytes=1_000_000, deadline_seconds=60)
    assert (
        post(client, "/v1/sources", {**data, "auth_ref": "env:AWS_SECRET_ACCESS_KEY"}).status_code
        == 403
    )
    assert (
        post(client, "/v1/sources", {**data, "url": "http://example.com/catalog"}).status_code
        == 403
    )
    assert post(client, "/v1/sources", data).status_code == 201


def test_every_retained_lifecycle_path_is_tenant_scoped(beta, source, recipe, snapshot):
    client, _, _, _, _ = beta
    recipe_id, key, queued, run = prepare(beta, source, recipe, snapshot)
    alice_audit = client.get("/v1/audit").json()
    assert alice_audit["items"]
    user(client, "bob")
    for path in (
        f"/v1/operations/{queued['id']}",
        f"/v1/runs/{run['id']}",
        f"/v1/runs/{run['id']}/records",
        f"/v1/revisions/{run['data']['revision_id']}/export",
        f"/v1/recipes/{recipe_id}",
    ):
        assert client.get(path).status_code == 404, path
    assert post(client, f"/v1/operations/{queued['id']}/cancel").status_code == 404
    assert post(client, f"/v1/runs/{run['id']}/replay").status_code == 404
    assert (
        post(
            client,
            "/v1/compile",
            {
                "source_id": source.id,
                "target_id": "none",
                "snapshot_hash": key,
                "identity_fields": ["/sku"],
            },
        ).status_code
        == 404
    )
    assert client.get("/v1/recipes").json()["items"] == []
    assert client.get("/v1/audit").json()["items"] == []
    assert (
        post(
            client,
            "/v1/storage/expand",
            {"limit_bytes": 999999999, "reason": "Must not raise free limit"},
        ).status_code
        == 403
    )
    assert post(client, "/v1/admin-tokens/rotate", {"overlap_seconds": 0}).status_code == 403


def test_idempotency_keys_are_tenant_scoped(beta):
    client = beta[0]
    for name in ("alice", "bob"):
        user(client, name)
        response = client.post(
            "/v1/schemas",
            json={"id": "same", "definition": {"type": "object"}},
            headers={"Idempotency-Key": "same-key"},
        )
        assert response.status_code == 201
        assert len(client.get("/v1/schemas").json()["items"]) == 1


def test_global_storage_reservations_pause_without_deletion(beta):
    client, db, raw, _, settings = beta
    user(client, "alice")
    with db.sessions.begin() as session:
        session.get(StorageQuota, "__global__").limit_bytes = 25
    first = client.put(
        "/v1/artifacts", content=b"private evidence A", headers={"Content-Type": "text/plain"}
    )
    assert first.status_code == 200
    user(client, "bob")
    second = client.put(
        "/v1/artifacts", content=b"private evidence B", headers={"Content-Type": "text/plain"}
    )
    assert second.status_code == 507
    assert raw.get(first.json()["hash"]) == b"private evidence A"
    with db.sessions() as session:
        assert session.get(StorageQuota, "__global__").used_bytes == len(b"private evidence A")
        assert len(session.scalars(select(Artifact)).all()) == 1


def test_public_app_construction_and_liveness_do_not_query_db(beta):
    _, db, raw, _, settings = beta
    queries = []

    def seen(*args):
        queries.append(args[2])

    event.listen(db.engine, "before_cursor_execute", seen)
    try:
        with TestClient(
            create_app(settings, db, raw, verifier=lambda _: {"sub": "alice"})
        ) as client:
            assert client.get("/health/live").status_code == 200
            assert client.get("/health/ready").status_code == 403
        assert queries == []
    finally:
        event.remove(db.engine, "before_cursor_execute", seen)


def test_api_admission_stops_at_global_budget(beta):
    client, db, _, _, settings = beta
    user(client, "alice")
    with db.sessions.begin() as session:
        global_row = session.get(
            BudgetUsage,
            (
                "__global__",
                __import__("datetime").datetime.now(__import__("datetime").UTC).strftime("%Y-%m"),
            ),
        )
        global_row.counters = {"api_requests": settings.beta_global_max_api_requests}
    assert client.get("/v1/sources").status_code == 507
    assert client.get("/health/live").status_code == 200


def test_label_snapshots_are_immutable_and_not_certified(beta, source, recipe, snapshot):
    client, _, raw, _, _ = beta
    _, _, queued, run = prepare(beta, source, recipe, snapshot)
    taxonomy = {
        "id": "tools",
        "nodes": [{"id": "drill", "label": "Drill"}, {"id": "saw", "label": "Saw"}],
    }
    assert post(client, "/v1/taxonomies", taxonomy).status_code == 201
    record_id = client.get(f"/v1/runs/{run['id']}/records").json()["items"][0]["id"]
    body = {
        "taxonomy_id": "tools",
        "revision_id": run["data"]["revision_id"],
        "record_id": record_id,
        "label_id": "drill",
        "provenance": "ai",
        "reason": "AI proposal against evidence",
    }
    correction = post(client, "/v1/labels", body)
    assert correction.status_code == 201, correction.text
    assert "record" not in correction.json()["data"]
    assert correction.json()["data"]["result_key"]
    assert (
        post(
            client, "/v1/datasets/snapshots", {"taxonomy_id": "tools", "provenance": "human"}
        ).status_code
        == 422
    )
    saved = post(client, "/v1/datasets/snapshots", {"taxonomy_id": "tools", "provenance": "ai"})
    assert saved.status_code == 201, saved.text
    saved = saved.json()
    before = client.get(f"/v1/datasets/{saved['id']}/export")
    payload = before.json()
    assert payload["rows"][0]["correction_id"] == correction.json()["id"]
    assert payload["rows"][0]["label_id"] == "drill"
    assert object_hash(payload["rows"][0]["record"]) == payload["rows"][0]["record_hash"]
    assert not payload["certification_eligible"] and not payload["auto_accept_enabled"]
    assert digest(before.content) == saved["data"]["dataset_hash"]
    assert object_hash(payload["taxonomy"]) == saved["data"]["taxonomy_hash"]
    model = {
        "id": "reviewed-candidate",
        "provider": "tfidf",
        "taxonomy_id": "tools",
        "dataset_id": saved["id"],
        "checkpoint_revision": "pinned-remote-commit",
        "artifact_uri": "https://registry.example.com/model",
        "artifact_hash": "a" * 64,
        "calibration_hash": "b" * 64,
        "evaluation_hash": "c" * 64,
        "license": "Apache-2.0",
    }
    registered = post(client, "/v1/models", model)
    assert registered.status_code == 201, registered.text
    assert registered.json()["data"]["state"] == "registered_not_certified"
    assert not registered.json()["data"]["automatic_promotion"]
    assert (
        post(
            client,
            "/v1/datasets/snapshots",
            {"taxonomy_id": "tools", "provenance": "ai", "label_ids": [correction.json()["id"]]},
        ).status_code
        == 201
    )
    assert (
        post(
            client,
            "/v1/datasets/snapshots",
            {"taxonomy_id": "tools", "provenance": "ai", "label_ids": ["not-owned"]},
        ).status_code
        == 422
    )
    newer = post(
        client,
        "/v1/labels",
        {
            **body,
            "supersedes": correction.json()["id"],
            "label_id": "saw",
            "provenance": "human",
            "reason": "Human corrected against evidence",
        },
    )
    assert newer.status_code == 201, newer.text
    assert client.get(f"/v1/datasets/{saved['id']}/export").content == before.content
    assert (
        post(
            client, "/v1/datasets/snapshots", {"taxonomy_id": "tools", "provenance": "ai"}
        ).status_code
        == 422
    )
    assert (
        post(
            client, "/v1/datasets/snapshots", {"taxonomy_id": "tools", "provenance": "human"}
        ).status_code
        == 201
    )
    user(client, "bob")
    assert client.get(f"/v1/datasets/{saved['id']}/export").status_code == 404
    assert post(client, "/v1/labels", body).status_code == 404
    assert post(client, "/v1/models", {**model, "id": "stolen"}).status_code == 404
    assert client.get("/v1/models/reviewed-candidate").status_code == 404


def test_metadata_quota_pauses_writes_without_reset_or_eviction(beta):
    client, db, _, _, settings = beta
    who = user(client, "alice")
    with db.sessions.begin() as session:
        lifetime = session.get(BudgetUsage, (who["workspace"], "total"))
        assert lifetime.counters["metadata_bytes"] >= 8192
        lifetime.counters = {"metadata_bytes": settings.beta_metadata_quota_bytes}
    response = post(client, "/v1/schemas", {"id": "blocked", "definition": {"type": "object"}})
    assert response.status_code == 507
    assert client.get("/v1/schemas").json()["items"] == []
    report = client.get("/v1/budget").json()
    assert report["workspace"]["metadata_period"] == "lifetime"
    assert report["workspace"]["reserved"]["metadata_bytes"] == settings.beta_metadata_quota_bytes


def test_public_json_body_is_bounded_before_database_admission(beta):
    client, db, _, _, _ = beta
    seen = []

    def query(*args):
        seen.append(True)

    event.listen(db.engine, "before_cursor_execute", query)
    try:
        response = client.post(
            "/v1/schemas",
            content=b"x" * 100_001,
            headers={"Content-Type": "application/json", "Authorization": "Bearer alice"},
        )
        assert response.status_code == 413
        assert seen == []
    finally:
        event.remove(db.engine, "before_cursor_execute", query)


def test_artifact_read_budget_blocks_export_without_deleting_evidence(
    beta, source, recipe, snapshot
):
    client, db, raw, _, _ = beta
    _, key, _, run = prepare(beta, source, recipe, snapshot)
    from product_pipeline.server.db import now

    with db.sessions.begin() as session:
        row = session.get(BudgetUsage, ("__global__", now().strftime("%Y-%m")))
        row.counters = {**row.counters, "artifact_read_bytes": 2_000_000_000}
    assert client.get(f"/v1/runs/{run['id']}/records").status_code == 507
    assert raw.get(key) == snapshot
    assert client.get(f"/v1/runs/{run['id']}").status_code == 200


def test_revoked_domain_is_rechecked_before_live_fetch(beta, source, recipe, snapshot):
    client, db, _, worker, settings = beta
    recipe_id, _, _, _ = prepare(beta, source, recipe, snapshot)
    queued = post(client, "/v1/runs", {"recipe_id": recipe_id}).json()
    with db.sessions.begin() as session:
        session.get(ApprovedDomain, "example.com").enabled = False

    def forbidden_fetch(_):
        pytest.fail("revoked source must not be fetched")

    worker.acquire = forbidden_fetch
    assert worker.once()
    operation = client.get(queued["location"]).json()
    assert operation["state"] == "failed"
    assert operation["error"]["title"] == "SOURCE_DENIED"
