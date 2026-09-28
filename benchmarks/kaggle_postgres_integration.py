"""Private Kaggle integration harness. Contains no data or model dependencies.

Prepare with scripts/kaggle_backend_payload.py after explicit code-upload consent.
The source bundle is unpacked only under /tmp; outputs are test evidence, not code.
"""

import base64
import hashlib
import io
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

BUNDLE_BASE64 = "__SOURCE_BUNDLE_BASE64__"
BUNDLE_SHA256 = "__SOURCE_BUNDLE_SHA256__"


def main():
    output = Path("/kaggle/working")
    if not output.is_dir():
        raise RuntimeError("Run this harness only in Kaggle; do not execute it locally")
    if BUNDLE_BASE64.startswith("__SOURCE_"):
        raise RuntimeError("Prepare an explicitly approved code-only payload first")
    report = {
        "schema_version": "postgres-integration/v1",
        "status": "running",
        "measured_on": "kaggle",
        "expected_postgres_major": 17,
        "source_bundle_sha256": BUNDLE_SHA256,
        "contains_datasets": False,
        "contains_credentials": False,
        "model_downloads_or_inference": False,
        "scope": "disposable loopback-only PostgreSQL; database-only restore, not full service recovery",
        "stages": [],
        "test_suites": [],
    }
    work = Path(tempfile.mkdtemp(prefix="evidence-pg-integration-"))
    project = work / "project"
    project.mkdir()
    env = {
        **os.environ,
        "UV_CACHE_DIR": str(work / "uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(work / "python"),
        "UV_LINK_MODE": "copy",
        "DEBIAN_FRONTEND": "noninteractive",
    }
    server_command = None
    cluster = None
    started = time.monotonic()

    def execute(label, command, *, extra_env=None, cwd=project, timeout=240):
        before = time.monotonic()
        result = subprocess.run(
            command,
            cwd=cwd,
            env={**env, **(extra_env or {})},
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        combined = result.stdout + result.stderr
        report["stages"].append(
            {
                "name": label,
                "exit_code": result.returncode,
                "elapsed_seconds": round(time.monotonic() - before, 3),
                "output_tail": combined[-(12000 if result.returncode else 1800) :],
            }
        )
        print(f"{label}: exit={result.returncode}", flush=True)
        if result.returncode:
            raise RuntimeError(f"{label} failed: {combined[-6000:]}")
        return result.stdout

    def suite(label, python, extra_env=None, only_quota=False):
        xml = output / f"{label}.xml"
        command = [str(python), "-m", "pytest", "tests/test_postgres.py", f"--junitxml={xml}"]
        if only_quota:
            command.extend(["-k", "quota"])
        execute(label, command, extra_env=extra_env, timeout=120)
        suites = ET.parse(xml).getroot().iter("testsuite")
        counts = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
        for entry in suites:
            for key in counts:
                counts[key] += int(entry.get(key, "0"))
        report["test_suites"].append({"name": label, **counts})
        assert counts["tests"] >= (1 if only_quota else 7), "test collection was incomplete"
        assert not any(counts[key] for key in ("failures", "errors", "skipped"))

    try:
        payload = base64.b64decode(BUNDLE_BASE64, validate=True)
        if hashlib.sha256(payload).hexdigest() != BUNDLE_SHA256:
            raise ValueError("source bundle hash mismatch")
        report["source_files"] = []
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for entry in archive.infolist():
                target = (project / entry.filename).resolve()
                if not target.is_relative_to(project.resolve()) or entry.is_dir():
                    raise ValueError("invalid source bundle entry")
                data = archive.read(entry)
                report["source_files"].append(
                    {
                        "path": entry.filename,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "bytes": len(data),
                    }
                )
            archive.extractall(project)
        if os.geteuid() != 0:
            raise RuntimeError(
                "this private disposable Kaggle harness requires its standard root runtime"
            )
        execute(
            "install_uv",
            [sys.executable, "-m", "pip", "install", "--quiet", "uv==0.12.13"],
            timeout=150,
        )
        execute(
            "locked_environment",
            [
                sys.executable,
                "-m",
                "uv",
                "sync",
                "--frozen",
                "--extra",
                "server",
                "--extra",
                "dev",
                "--python",
                "3.13",
            ],
            timeout=360,
        )
        python = project / ".venv/bin/python"
        report["runtime"] = json.loads(
            execute(
                "runtime_versions",
                [
                    str(python),
                    "-c",
                    "import importlib.metadata as m,json,sys; print(json.dumps({'python':sys.version,'packages':{p:m.version(p) for p in ['sqlalchemy','psycopg','alembic','pytest']}}))",
                ],
            )
        )
        pg_bin = Path("/usr/lib/postgresql/17/bin")
        if not (pg_bin / "pg_ctl").exists():
            execute("apt_update", ["apt-get", "update", "-qq"], timeout=180)
            execute(
                "postgres_repository_prerequisites",
                ["apt-get", "install", "-y", "--no-install-recommends", "curl", "ca-certificates"],
                timeout=300,
            )
            # Follow the PostgreSQL project's signed APT repository instructions:
            # https://www.postgresql.org/download/linux/ubuntu/
            codename = platform.freedesktop_os_release()["VERSION_CODENAME"]
            if codename not in {"jammy", "noble", "resolute", "stonking"}:
                raise RuntimeError("Kaggle Ubuntu version is not in the supported PGDG allowlist")
            architecture = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
            key = Path("/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc")
            key.parent.mkdir(parents=True, exist_ok=True)
            execute(
                "postgres_repository_key",
                [
                    "curl",
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--location",
                    "--output",
                    str(key),
                    "https://www.postgresql.org/media/keys/ACCC4CF8.asc",
                ],
            )
            Path("/etc/apt/sources.list.d/pgdg.sources").write_text(
                f"Types: deb\nURIs: https://apt.postgresql.org/pub/repos/apt\nSuites: {codename}-pgdg\nArchitectures: {architecture}\nComponents: main\nSigned-By: {key}\n",
                encoding="utf-8",
            )
            execute("postgres_repository_update", ["apt-get", "update", "-qq"], timeout=180)
            execute(
                "install_postgres_17",
                ["apt-get", "install", "-y", "--no-install-recommends", "postgresql-17"],
                timeout=300,
            )
        cluster = Path(tempfile.mkdtemp(prefix="evidence-pg-cluster-"))
        shutil.chown(cluster, user="postgres", group="postgres")
        server_command = ["runuser", "-u", "postgres", "--", str(pg_bin / "pg_ctl")]
        execute(
            "initialize_postgres",
            [
                "runuser",
                "-u",
                "postgres",
                "--",
                str(pg_bin / "initdb"),
                "-D",
                str(cluster),
                "-A",
                "trust",
                "--no-locale",
                "--encoding=UTF8",
            ],
            cwd=Path("/tmp"),
        )
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        execute(
            "start_postgres",
            server_command
            + [
                "-D",
                str(cluster),
                "-l",
                str(cluster / "server.log"),
                "-o",
                f"-h 127.0.0.1 -p {port} -c unix_socket_directories={cluster} -c statement_timeout=30000 -c lock_timeout=10000",
                "-w",
                "start",
            ],
            cwd=Path("/tmp"),
        )
        connection = ["-h", "127.0.0.1", "-p", str(port), "-U", "postgres"]
        execute(
            "create_database", [str(pg_bin / "createdb"), *connection, "evidence_pipeline_test"]
        )
        database_url = f"postgresql+psycopg://postgres@127.0.0.1:{port}/evidence_pipeline_test"
        env.update(PIPELINE_DATABASE_URL=database_url, PIPELINE_TEST_DATABASE_URL=database_url)
        report["postgres_version"] = execute(
            "postgres_version",
            [
                str(pg_bin / "psql"),
                *connection,
                "-d",
                "evidence_pipeline_test",
                "-Atc",
                "SELECT version()",
            ],
        ).strip()
        if not report["postgres_version"].startswith("PostgreSQL 17."):
            raise RuntimeError("integration target must match deployment PostgreSQL 17")
        execute("migrate", [str(python), "-m", "product_pipeline.cli", "migrate"])
        execute("migrate_repeat", [str(python), "-m", "product_pipeline.cli", "migrate"])
        execute("schema_drift_check", [str(python), "-m", "alembic", "check"])
        suite("postgres-primary", python)
        for repeat in range(5):
            suite(f"postgres-quota-repeat-{repeat + 1}", python, only_quota=True)
        dump = work / "database-test.dump"
        execute(
            "database_backup",
            [
                str(pg_bin / "pg_dump"),
                *connection,
                "--format=custom",
                "--file",
                str(dump),
                "evidence_pipeline_test",
            ],
        )
        execute(
            "create_restore_database",
            [str(pg_bin / "createdb"), *connection, "evidence_pipeline_restored"],
        )
        execute(
            "database_restore",
            [
                str(pg_bin / "pg_restore"),
                *connection,
                "--exit-on-error",
                "-d",
                "evidence_pipeline_restored",
                str(dump),
            ],
        )
        restored = database_url.replace("/evidence_pipeline_test", "/evidence_pipeline_restored")
        restore_env = {"PIPELINE_DATABASE_URL": restored, "PIPELINE_TEST_DATABASE_URL": restored}
        execute(
            "restored_schema_drift_check",
            [str(python), "-m", "alembic", "check"],
            extra_env=restore_env,
        )
        suite("postgres-restored", python, extra_env=restore_env)
        report["status"] = "completed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc()[-12000:],
        }
    finally:
        if server_command and cluster:
            try:
                execute(
                    "stop_postgres",
                    server_command + ["-D", str(cluster), "-m", "fast", "-w", "stop"],
                    cwd=Path("/tmp"),
                    timeout=30,
                )
            except Exception as exc:
                report["cleanup_warning"] = str(exc)
                report["status"] = "failed"
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (output / "postgres-integration-report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print("POSTGRES_INTEGRATION_REPORT_JSON=" + json.dumps(report), flush=True)
    if report["status"] != "completed":
        raise RuntimeError("PostgreSQL integration failed; inspect the retained report")


if __name__ == "__main__":
    main()
