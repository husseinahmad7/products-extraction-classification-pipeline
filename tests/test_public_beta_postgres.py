"""Isolated-schema integration tests, only in an explicitly disposable test database."""

import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from product_pipeline import server
from product_pipeline.errors import PipelineError
from product_pipeline.server.api import create_app
from product_pipeline.server.artifacts import QuotaStore
from product_pipeline.server.db import BudgetUsage, Database, Resource, StorageQuota, now
from product_pipeline.server.public_beta import reserve
from product_pipeline.server.settings import Settings
from product_pipeline.storage import LocalStore

pytestmark = pytest.mark.skipif(
    not os.getenv("PIPELINE_TEST_DATABASE_URL"),
    reason="disposable PostgreSQL database not configured",
)


@pytest.fixture
def pg():
    url = make_url(os.environ["PIPELINE_TEST_DATABASE_URL"])
    assert url.get_backend_name() == "postgresql"
    schema = "beta_test_" + uuid4().hex
    control = create_engine(url)
    with control.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = url.update_query_dict({"options": "-csearch_path=" + schema}).render_as_string(
        hide_password=False
    )
    config = Config()
    config.set_main_option("script_location", str(Path(server.__file__).parent / "migrations"))
    config.attributes["database_url"] = isolated
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    command.check(config)
    db = Database(isolated)
    try:
        yield db
    finally:
        db.engine.dispose()
        # Only the exact random schema created by this fixture in the disposable DB.
        assert re.fullmatch(r"beta_test_[a-f0-9]{32}", schema)
        with control.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()


def test_global_admission_serializes_across_tenants(pg):
    settings = Settings(beta_global_max_jobs=3, beta_max_jobs=2)

    def admit(index):
        try:
            with pg.sessions.begin() as session:
                reserve(session, "tenant-" + str(index % 2), {"jobs": 1}, settings)
            return True
        except PipelineError as exc:
            assert exc.code == "QUOTA_EXCEEDED"
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(admit, range(8))) == 3
    with pg.sessions() as session:
        rows = session.scalars(
            select(BudgetUsage).where(BudgetUsage.period == now().strftime("%Y-%m"))
        ).all()
        global_count = next(row.counters["jobs"] for row in rows if row.scope == "__global__")
        assert (
            sum(row.counters["jobs"] for row in rows if row.scope != "__global__")
            == global_count
            == 3
        )
        assert all(row.counters["jobs"] <= 2 for row in rows if row.scope != "__global__")


def test_global_artifact_reservations_cannot_oversubscribe(pg, tmp_path):
    raw = LocalStore(tmp_path)
    stores = [QuotaStore(raw, pg, "tenant-" + str(i), 8, global_limit_bytes=12) for i in range(4)]

    def retain(i):
        try:
            stores[i % 4].put(f"obj{i}".encode())
            return True
        except PipelineError as exc:
            assert exc.code == "QUOTA_EXCEEDED"
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(retain, range(8))) == 3
    with pg.sessions() as session:
        rows = session.scalars(select(StorageQuota)).all()
        assert next(row.used_bytes for row in rows if row.workspace == "__global__") == 12
        assert sum(row.used_bytes for row in rows if row.workspace != "__global__") == 12


def test_metadata_is_lifetime_and_rejection_rolls_back_both_scopes(pg):
    settings = Settings(beta_metadata_quota_bytes=16, beta_global_metadata_quota_bytes=24)
    for tenant in ("alice", "alice", "bob"):
        with pg.sessions.begin() as session:
            reserve(session, tenant, {"metadata_bytes": 8}, settings)
    with pytest.raises(PipelineError), pg.sessions.begin() as session:
        reserve(session, "charlie", {"metadata_bytes": 1, "jobs": 1}, settings)
    with pg.sessions() as session:
        assert session.get(BudgetUsage, ("__global__", "total")).counters == {"metadata_bytes": 24}
        assert session.get(BudgetUsage, ("charlie", "total")) is None
        assert session.get(BudgetUsage, ("__global__", now().strftime("%Y-%m"))) is None


@pytest.mark.parametrize("kind", ["label", "dataset", "model"])
def test_learning_versions_immutable_at_database_boundary(pg, kind):
    with pg.sessions.begin() as session:
        session.add(Resource(workspace="alice", kind=kind, id="frozen", data={"hash": "original"}))
    with pytest.raises(DBAPIError, match="immutable resource"), pg.sessions.begin() as session:
        session.execute(
            update(Resource).where(Resource.kind == kind).values(data={"hash": "tampered"})
        )


def test_public_api_ownership_and_idempotency_on_postgres(pg, tmp_path):
    settings = Settings(
        auth_mode="neon",
        neon_auth_issuer="https://auth.example.com",
        neon_auth_jwks_url="https://auth.example.com/jwks",
    )
    app = create_app(
        settings,
        pg,
        LocalStore(tmp_path),
        verifier=lambda token: {"sub": token, "workspace": "forged-shared", "role": "admin"},
    )
    with TestClient(app) as client:
        owners = []
        for person in ("alice", "bob"):
            client.headers["Authorization"] = "Bearer " + person
            who = client.get("/v1/me").json()
            assert not who["is_operator"]
            owners.append(who["workspace"])
            assert (
                client.post(
                    "/v1/schemas",
                    json={"id": person, "definition": {"type": "object"}},
                    headers={"Idempotency-Key": "same-key"},
                ).status_code
                == 201
            )
            assert client.get("/v1/schemas").json()["items"][0]["id"] == person
        assert owners[0] != owners[1]
        assert client.get("/v1/schemas/alice").status_code == 404
        assert client.get("/health/ready").status_code == 403
        assert client.get("/health/live").status_code == 200
