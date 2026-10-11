"""Opt-in, credential-redacted hosting compatibility probes; never downloads models.

No resources are provisioned. PostgreSQL is read-only. The S3 probe writes and
removes only uniquely named synthetic objects in an explicitly supplied private
probe bucket. A local PASS must never be presented as Wasmer compatibility.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import platform
import re
import sys
import tempfile
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .errors import PipelineError
from .hashing import digest

DEPENDENCIES = {
    "pydantic": "pydantic",
    "pydantic-core": "pydantic_core",
    "jsonschema": "jsonschema",
    "rfc8785": "rfc8785",
    "beautifulsoup4": "bs4",
    "httpx": "httpx",
    "pyyaml": "yaml",
    "regex": "regex",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "sqlalchemy": "sqlalchemy",
    "psycopg": "psycopg",
    "psycopg-binary": "psycopg_binary",
    "boto3": "boto3",
    "argon2-cffi": "argon2",
    "greenlet": "greenlet",
    "alembic": "alembic",
    "pyjwt": "jwt",
    "cryptography": "cryptography",
}


def not_run(reason: str) -> dict:
    return {"status": "NOT_RUN", "reason": reason}


def safe_failure(exc: Exception) -> dict:
    # Driver exception messages can include DSNs, URL signatures and secrets.
    return {"status": "FAIL", "error_type": type(exc).__name__}


def greenlet_required() -> bool:
    """SQLAlchemy 2.1's asyncio extra is not part of this sync server."""
    try:
        requirements = importlib.metadata.requires("sqlalchemy") or []
    except importlib.metadata.PackageNotFoundError:
        return False
    return any(
        requirement.lower().startswith("greenlet") and "extra" not in requirement.partition(";")[2]
        for requirement in requirements
    )


def probe_dependencies(lock_path: Path) -> dict:
    expected: dict[str, set[str]] = {}
    lock_hash = None
    try:
        lock_bytes = lock_path.read_bytes()
        lock_hash = digest(lock_bytes)
        for package in tomllib.loads(lock_bytes.decode())["package"]:
            if package.get("version"):
                expected.setdefault(package["name"], set()).add(package["version"])
    except (OSError, KeyError, ValueError):
        pass
    checks: dict[str, dict] = {}
    for distribution, module in DEPENDENCIES.items():
        if distribution == "greenlet" and not greenlet_required():
            checks[distribution] = {
                "status": "NOT_REQUIRED",
                "reason": "sync SQLAlchemy runtime does not require the optional asyncio dependency",
            }
            continue
        try:
            importlib.import_module(module)
            version = importlib.metadata.version(distribution)
            pinned = sorted(expected.get(distribution, set()))
            checks[distribution] = {
                "status": "PASS" if version in pinned else "FAIL" if pinned else "NOT_RUN",
                "installed_version": version,
                "locked_versions": pinned,
            }
        except Exception as exc:
            checks[distribution] = safe_failure(exc)
    status = (
        "FAIL"
        if any(check["status"] == "FAIL" for check in checks.values())
        else "PASS"
        if lock_hash
        and all(check["status"] in {"PASS", "NOT_REQUIRED"} for check in checks.values())
        else "NOT_RUN"
    )
    return {"status": status, "lock_sha256": lock_hash, "checks": checks}


def probe_native_behaviour() -> dict:
    try:
        import regex
        from argon2 import PasswordHasher
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from pydantic import BaseModel
        from sqlalchemy import create_engine, text

        class Sample(BaseModel):
            value: int

        assert Sample.model_validate_json('{"value":7}').value == 7
        assert regex.search(r"\d+", "value 7", timeout=0.01).group() == "7"
        hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
        assert hasher.verify(hasher.hash("synthetic-probe-only"), "synthetic-probe-only")
        if greenlet_required():
            from greenlet import greenlet

            assert greenlet(lambda: "switched").switch() == "switched"
        private_key = Ed25519PrivateKey.generate()
        signature = private_key.sign(b"synthetic JWT probe")
        private_key.public_key().verify(signature, b"synthetic JWT probe")
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                assert connection.execute(text("SELECT 1")).scalar_one() == 1
        finally:
            engine.dispose()
        from .storage import LocalStore

        with tempfile.TemporaryDirectory(prefix="pipeline-hosting-probe-") as directory:
            store = LocalStore(Path(directory))
            key = store.put(b"synthetic evidence")
            assert store.put(b"synthetic evidence") == key
            assert store.get(key) == b"synthetic evidence"
        return {
            "status": "PASS",
            "checks": [
                "pydantic",
                "regex",
                "argon2",
                *(["greenlet"] if greenlet_required() else []),
                "ed25519",
                "sqlalchemy",
                "atomic_local_store",
            ],
        }
    except Exception as exc:
        return safe_failure(exc)


def probe_app_startup() -> dict:
    started = time.monotonic()
    try:
        from fastapi.testclient import TestClient
        from sqlalchemy import event

        from .server.api import create_app
        from .server.db import Database
        from .server.settings import Settings

        with tempfile.TemporaryDirectory(prefix="pipeline-app-probe-") as directory:
            for mode in ("admin", "neon"):
                db = Database(f"sqlite:///{Path(directory) / (mode + '.db')}")
                db.initialize()
                queries: list[bool] = []

                def record_query(*args, queries=queries):
                    queries.append(True)

                event.listen(db.engine, "before_cursor_execute", record_query)
                settings = Settings(
                    artifact_root=str(Path(directory) / mode / "artifacts"),
                    auth_mode=mode,
                    neon_auth_issuer="https://auth.invalid",
                    neon_auth_jwks_url="https://auth.invalid/.well-known/jwks.json",
                )
                try:
                    app = create_app(settings, db)
                    if mode == "neon":
                        assert not queries, "public app construction touched the database"
                    queries.clear()
                    with TestClient(app) as client:
                        assert client.get("/health/live").status_code == 200
                    assert not queries, "liveness probe touched the database"
                finally:
                    db.engine.dispose()
        return {
            "status": "PASS",
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "liveness_db_queries": 0,
            "public_startup_db_queries": 0,
        }
    except Exception as exc:
        return safe_failure(exc)


def probe_postgres(url: str | None) -> dict:
    if not url:
        return not_run("PIPELINE_DATABASE_URL is absent")
    engine = None
    started = time.monotonic()
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url

        parsed = make_url(url)
        if parsed.get_backend_name() != "postgresql":
            return not_run("configured database is not PostgreSQL")
        engine = create_engine(url, connect_args={"connect_timeout": 10}, pool_pre_ping=True)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1
            version: str = connection.exec_driver_sql("SHOW server_version").scalar_one()
        return {
            "status": "PASS",
            "first_connection_ms": round((time.monotonic() - started) * 1000, 3),
            "server_version": version,
            "cold_start_verified": False,
            "note": "A successful connection is not proof that the provider had suspended compute.",
        }
    except Exception as exc:
        return safe_failure(exc)
    finally:
        if engine is not None:
            engine.dispose()


def probe_neon_idle_resume(
    url: str | None,
    api_key: str | None,
    project_id: str | None,
    endpoint_id: str | None,
    idle_seconds: int = 310,
) -> dict:
    """Use only management GETs during quiet time, then measure a real resume.

    This deliberately takes >5 minutes when opted into with credentials. It
    never suspends/resizes a compute or changes a provider setting.
    """
    if not url or not api_key or not project_id or not endpoint_id:
        return not_run(
            "inject DATABASE_URL and NEON_API_KEY/NEON_PROJECT_ID/NEON_ENDPOINT_ID for the opt-in idle probe"
        )
    if (
        idle_seconds < 301
        or not re.fullmatch(r"[a-z0-9-]{1,60}", project_id or "")
        or not re.fullmatch(r"ep-[a-z0-9-]{1,57}", endpoint_id or "")
    ):
        return {
            "status": "FAIL",
            "reason": "invalid probe IDs or idle window shorter than 301 seconds",
        }
    try:
        import httpx
        from sqlalchemy.engine import make_url

        with httpx.Client(timeout=15, follow_redirects=False) as client:
            management_url = (
                f"https://console.neon.tech/api/v2/projects/{project_id}/endpoints/{endpoint_id}"
            )

            def endpoint_state():
                response = client.get(
                    management_url, headers={"Authorization": f"Bearer {api_key}"}
                )
                response.raise_for_status()
                endpoint = response.json()["endpoint"]
                assert endpoint["id"] == endpoint_id and endpoint["project_id"] == project_id
                return endpoint

            endpoint = endpoint_state()
            # Verify this API observation refers to the actual tested DSN,
            # accepting Neon's direct and pooled form of the same endpoint.
            tested_host = make_url(url).host or ""
            normalized_host = tested_host.replace(f"{endpoint_id}-pooler.", f"{endpoint_id}.", 1)
            assert normalized_host == endpoint["host"]
            assert endpoint["disabled"] is False
            if endpoint["suspend_timeout_seconds"] not in {0, 300}:
                return not_run("endpoint does not use the Free-plan five-minute idle timeout")
            initial = probe_postgres(url)
            if initial["status"] != "PASS":
                return initial
            started = time.monotonic()
            # No DB queries or connections are held during this interval.
            while (remaining := idle_seconds - (time.monotonic() - started)) > 0:
                time.sleep(min(30, remaining))
            endpoint = endpoint_state()
            if endpoint["current_state"] != "idle":
                return {
                    "status": "FAIL",
                    "reason": "compute remained active after quiet window; check workers, dashboards and external clients",
                }
            resumed = probe_postgres(url)
            if resumed["status"] != "PASS":
                return resumed
            return {
                "status": "PASS",
                "cold_start_verified": True,
                "observed_state_before_resume": "idle",
                "quiet_seconds": idle_seconds,
                "resume_connection_ms": resumed["first_connection_ms"],
            }
    except Exception as exc:
        return safe_failure(exc)


def probe_s3_client(client, bucket: str) -> dict:
    """Exercise required semantics, then remove only this run's synthetic keys."""
    from botocore.exceptions import ClientError

    from .storage import S3Store

    probe_id = str(uuid4())
    conditional_key = f"compatibility-probe/{probe_id}/conditional"
    race_key = f"compatibility-probe/{probe_id}/race"
    content = ("pipeline synthetic probe " + probe_id).encode()
    artifact_key = digest(content)
    keys = [conditional_key, race_key, artifact_key]
    cleanup_keys: list[str] = []
    report: dict = {"status": "FAIL", "probe_id": probe_id}
    try:
        # A private probe bucket is required. Do not mutate existing artifacts,
        # even in the cryptographically improbable event of a key collision.
        for key in keys:
            try:
                client.head_object(Bucket=bucket, Key=key)
            except ClientError as exc:
                if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                    raise
            else:
                return {
                    "status": "FAIL",
                    "reason": "synthetic key already exists",
                    "probe_id": probe_id,
                }

        # Only targets proven absent before this unique probe may be removed.
        cleanup_keys = keys

        response = client.put_object(
            Bucket=bucket,
            Key=conditional_key,
            Body=content,
            IfNoneMatch="*",
            ServerSideEncryption="AES256",
        )
        assert response.get("ServerSideEncryption") == "AES256"
        try:
            client.put_object(
                Bucket=bucket,
                Key=conditional_key,
                Body=b"must not replace evidence",
                IfNoneMatch="*",
                ServerSideEncryption="AES256",
            )
        except ClientError as exc:
            assert exc.response["ResponseMetadata"]["HTTPStatusCode"] == 412
        else:
            raise AssertionError("conditional overwrite was accepted")
        assert client.get_object(Bucket=bucket, Key=conditional_key)["Body"].read() == content
        assert (
            client.head_object(Bucket=bucket, Key=conditional_key).get("ServerSideEncryption")
            == "AES256"
        )
        barrier = threading.Barrier(2)

        def race_write(body):
            barrier.wait(timeout=10)
            try:
                result = client.put_object(
                    Bucket=bucket,
                    Key=race_key,
                    Body=body,
                    IfNoneMatch="*",
                    ServerSideEncryption="AES256",
                )
                return result["ResponseMetadata"]["HTTPStatusCode"], body
            except ClientError as exc:
                return exc.response["ResponseMetadata"]["HTTPStatusCode"], None

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(race_write, [b"race one", b"race two"]))
        statuses = sorted(status for status, _ in outcomes)
        assert statuses == [200, 412], (
            "existing S3Store requires a 412 for the losing conditional write"
        )
        winner = next(body for status, body in outcomes if status == 200)
        assert client.get_object(Bucket=bucket, Key=race_key)["Body"].read() == winner
        # Exercise the actual application adapter, not a weaker upload path.
        store = object.__new__(S3Store)
        store.client, store.bucket = client, bucket
        assert store.put(content) == artifact_key
        assert store.put(content) == artifact_key
        assert store.get(artifact_key) == content
        # Intentional corruption is confined to this unique synthetic object.
        client.put_object(
            Bucket=bucket, Key=artifact_key, Body=b"tampered probe", ServerSideEncryption="AES256"
        )
        try:
            store.get(artifact_key)
        except PipelineError as exc:
            assert exc.code == "ARTIFACT_CORRUPT"
        else:
            raise AssertionError("artifact hash mismatch was accepted")
        report = {
            "status": "PASS",
            "probe_id": probe_id,
            "checks": [
                "conditional_put",
                "sse_aes256_echo",
                "concurrent_creation",
                "adapter_deduplication",
                "digest_verification",
            ],
            "race_statuses": statuses,
        }
    except Exception as exc:
        report = {**safe_failure(exc), "probe_id": probe_id}
    finally:
        cleanup_ok = True
        for key in cleanup_keys:
            try:
                client.delete_object(Bucket=bucket, Key=key)
            except Exception:
                cleanup_ok = False
        report["cleanup"] = "PASS" if cleanup_ok else "FAIL"
        if not cleanup_ok:
            report["status"] = "FAIL"
    return report


def probe_s3(bucket: str | None, endpoint: str | None) -> dict:
    if not bucket or not endpoint:
        return not_run(
            "set PIPELINE_PROBE_S3_BUCKET and PIPELINE_S3_ENDPOINT for a private test bucket"
        )
    if not os.getenv("AWS_ACCESS_KEY_ID") or not os.getenv("AWS_SECRET_ACCESS_KEY"):
        return not_run("explicit environment S3 credentials are absent")
    client = None
    try:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=10,
                read_timeout=10,
                retries={"max_attempts": 0},
            ),
        )
        return probe_s3_client(client, bucket)
    except Exception as exc:
        return safe_failure(exc)
    finally:
        if client is not None:
            client.close()


def collect_report(
    lock_path: Path,
    runtime: str = "local",
    postgres: bool = False,
    s3_write: bool = False,
    neon_idle: bool = False,
    idle_seconds: int = 310,
) -> dict:
    dependencies = probe_dependencies(lock_path)
    native = probe_native_behaviour()
    startup = probe_app_startup()
    observed_wasm = "wasm" in platform.machine().lower() or sys.platform in {"wasi", "wasix"}
    wasmer = (
        {"status": "PASS", "observed_wasm": True}
        if runtime == "wasmer"
        and observed_wasm
        and dependencies["status"] == native["status"] == startup["status"] == "PASS"
        else not_run(
            "run this locked build inside Wasmer/WASIX; native tests do not prove compatibility"
        )
    )
    checks = {
        "dependencies": dependencies,
        "native_behaviour": native,
        "application_startup": startup,
        "wasmer_runtime": wasmer,
        "postgres_connection": probe_postgres(os.getenv("PIPELINE_DATABASE_URL"))
        if postgres
        else not_run("remote PostgreSQL probe not requested"),
        "s3_evidence": probe_s3(
            os.getenv("PIPELINE_PROBE_S3_BUCKET"), os.getenv("PIPELINE_S3_ENDPOINT")
        )
        if s3_write
        else not_run("remote S3 writes not requested"),
        "idle_suspension_and_cold_resume": probe_neon_idle_resume(
            os.getenv("PIPELINE_DATABASE_URL"),
            os.getenv("NEON_API_KEY"),
            os.getenv("NEON_PROJECT_ID"),
            os.getenv("NEON_ENDPOINT_ID"),
            idle_seconds,
        )
        if neon_idle
        else not_run(
            "opt into --neon-idle-probe to verify >5 minutes with no DB queries, provider-observed suspension and resume"
        ),
    }
    return {
        "schema_version": "hosting-probe/v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "target_runtime": runtime,
        "observed_platform": sys.platform,
        "python_version": platform.python_version(),
        "checks": checks,
        "deployment_ready": all(check["status"] == "PASS" for check in checks.values()),
        "note": "No models downloaded, no resources provisioned, no credentials or endpoint URLs emitted.",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("uv.lock"))
    parser.add_argument("--runtime", choices=["local", "wasmer"], default="local")
    parser.add_argument(
        "--postgres",
        action="store_true",
        help="read-only connection probe using injected PIPELINE_DATABASE_URL",
    )
    parser.add_argument(
        "--s3-write-probe",
        action="store_true",
        help="write/delete synthetic objects in injected private PIPELINE_PROBE_S3_BUCKET",
    )
    parser.add_argument(
        "--neon-idle-probe",
        action="store_true",
        help="opt-in >5 minute quiet test plus management-API observed suspend/resume",
    )
    parser.add_argument("--idle-seconds", type=int, default=310)
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="fail closed when any deployment acceptance check is pending",
    )
    args = parser.parse_args(argv)
    report = collect_report(
        args.lock,
        args.runtime,
        args.postgres,
        args.s3_write_probe,
        args.neon_idle_probe,
        args.idle_seconds,
    )
    print(json.dumps(report, indent=2))
    if args.require_ready and not report["deployment_ready"]:
        return 2
    return 1 if any(check["status"] == "FAIL" for check in report["checks"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
