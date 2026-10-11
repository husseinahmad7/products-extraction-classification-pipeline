import io
import json
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from product_pipeline import hosting_probe as probe


def client_error(status):
    return ClientError(
        {"ResponseMetadata": {"HTTPStatusCode": status}, "Error": {"Code": str(status)}},
        "SyntheticProbe",
    )


class FakeS3:
    def __init__(self, ignore_conditional=False, echo_encryption=True, fail_cleanup=False):
        self.objects = {}
        self.deleted = []
        self.lock = threading.Lock()
        self.ignore_conditional = ignore_conditional
        self.echo_encryption = echo_encryption
        self.fail_cleanup = fail_cleanup

    def head_object(self, *, Bucket, Key):
        with self.lock:
            if Key not in self.objects:
                raise client_error(404)
            return {"ServerSideEncryption": "AES256"} if self.echo_encryption else {}

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch=None, ServerSideEncryption=None):
        assert ServerSideEncryption == "AES256"
        with self.lock:
            if IfNoneMatch == "*" and Key in self.objects and not self.ignore_conditional:
                raise client_error(412)
            self.objects[Key] = Body
            return {
                "ResponseMetadata": {"HTTPStatusCode": 200},
                **({"ServerSideEncryption": "AES256"} if self.echo_encryption else {}),
            }

    def get_object(self, *, Bucket, Key):
        with self.lock:
            return {"Body": io.BytesIO(self.objects[Key])}

    def delete_object(self, *, Bucket, Key):
        with self.lock:
            if self.fail_cleanup:
                raise RuntimeError("secret=must-not-appear")
            self.deleted.append(Key)
            self.objects.pop(Key, None)


def test_s3_probe_required_semantics_and_scoped_cleanup():
    client = FakeS3()
    client.objects["existing-customer-evidence"] = b"retain me"
    report = probe.probe_s3_client(client, "private-test")
    assert report["status"] == report["cleanup"] == "PASS"
    assert report["race_statuses"] == [200, 412]
    assert client.objects == {"existing-customer-evidence": b"retain me"}
    assert len(client.deleted) == 3 and "existing-customer-evidence" not in client.deleted


@pytest.mark.parametrize(
    "kwargs", [{"ignore_conditional": True}, {"echo_encryption": False}, {"fail_cleanup": True}]
)
def test_s3_does_not_accept_weaker_semantics_or_leak_failures(kwargs):
    report = probe.probe_s3_client(FakeS3(**kwargs), "private-test")
    assert report["status"] == "FAIL"
    assert "secret=" not in json.dumps(report)


def test_s3_probe_does_not_delete_a_colliding_existing_object(monkeypatch):
    monkeypatch.setattr(probe, "uuid4", lambda: "fixed-id")
    key = "compatibility-probe/fixed-id/conditional"
    client = FakeS3()
    client.objects[key] = b"existing"
    report = probe.probe_s3_client(client, "private-test")
    assert report["status"] == "FAIL"
    assert client.objects == {key: b"existing"} and not client.deleted


def test_no_credentials_are_reported_not_run(monkeypatch):
    for key in (
        "PIPELINE_DATABASE_URL",
        "PIPELINE_S3_ENDPOINT",
        "PIPELINE_PROBE_S3_BUCKET",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "NEON_API_KEY",
        "NEON_PROJECT_ID",
        "NEON_ENDPOINT_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    assert probe.probe_postgres(None)["status"] == "NOT_RUN"
    assert probe.probe_postgres("sqlite:///unused.db")["status"] == "NOT_RUN"
    assert probe.probe_s3(None, None)["status"] == "NOT_RUN"
    assert probe.probe_s3("private-test", "https://storage.invalid")["status"] == "NOT_RUN"
    assert probe.probe_neon_idle_resume(None, None, None, None)["status"] == "NOT_RUN"


def test_driver_failure_is_redacted():
    report = probe.probe_postgres("not-a-dsn password=must-not-appear")
    assert report["status"] == "FAIL"
    assert "must-not-appear" not in json.dumps(report)


def mock_local_checks(monkeypatch):
    monkeypatch.setattr(
        probe, "probe_dependencies", lambda path: {"status": "PASS", "lock_sha256": "a" * 64}
    )
    monkeypatch.setattr(probe, "probe_native_behaviour", lambda: {"status": "PASS"})
    monkeypatch.setattr(probe, "probe_app_startup", lambda: {"status": "PASS"})


def test_native_success_never_claims_wasmer_compatibility(monkeypatch):
    mock_local_checks(monkeypatch)
    monkeypatch.setattr(probe.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(probe.sys, "platform", "linux")
    remote = Mock(side_effect=AssertionError("default probe must not access remote resources"))
    monkeypatch.setattr(probe, "probe_postgres", remote)
    monkeypatch.setattr(probe, "probe_s3", remote)
    report = probe.collect_report(Path("uv.lock"), "wasmer")
    assert report["checks"]["wasmer_runtime"]["status"] == "NOT_RUN"
    assert report["checks"]["postgres_connection"]["status"] == "NOT_RUN"
    assert report["deployment_ready"] is False
    remote.assert_not_called()


def test_require_ready_fails_closed_on_pending(monkeypatch, capsys):
    mock_local_checks(monkeypatch)
    assert probe.main(["--require-ready"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert not report["deployment_ready"]


def test_dependency_versions_match_the_lock(tmp_path, monkeypatch):
    lock = tmp_path / "uv.lock"
    lock.write_text('[[package]]\nname="pydantic"\nversion="2.12.0"\n', encoding="utf-8")
    monkeypatch.setattr(probe, "DEPENDENCIES", {"pydantic": "pydantic"})
    monkeypatch.setattr(probe.importlib, "import_module", lambda name: object())
    monkeypatch.setattr(probe.importlib.metadata, "version", lambda name: "2.12.0")
    assert probe.probe_dependencies(lock)["status"] == "PASS"
    monkeypatch.setattr(probe.importlib.metadata, "version", lambda name: "2.11.0")
    assert probe.probe_dependencies(lock)["status"] == "FAIL"
    assert probe.probe_dependencies(tmp_path / "missing.lock")["status"] == "NOT_RUN"


def test_neon_idle_probe_observes_actual_endpoint_and_avoids_db_during_wait(monkeypatch):
    import httpx

    clock = [0.0]
    connection = Mock(return_value={"status": "PASS", "first_connection_ms": 250})
    monkeypatch.setattr(probe, "probe_postgres", connection)
    monkeypatch.setattr(probe.time, "monotonic", lambda: clock[0])

    def sleep(seconds):
        assert connection.call_count == 1
        assert seconds <= 30
        clock[0] += seconds

    monkeypatch.setattr(probe.time, "sleep", sleep)
    states = iter(["active", "idle"])

    class ManagementClient:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers):
            assert url == "https://console.neon.tech/api/v2/projects/project-one/endpoints/ep-one"
            assert headers["Authorization"] == "Bearer secret-test-only"
            endpoint = {
                "id": "ep-one",
                "project_id": "project-one",
                "host": "ep-one.aws.neon.tech",
                "disabled": False,
                "suspend_timeout_seconds": 300,
                "current_state": next(states),
            }
            return httpx.Response(
                200, json={"endpoint": endpoint}, request=httpx.Request("GET", url)
            )

    monkeypatch.setattr(httpx, "Client", ManagementClient)
    report = probe.probe_neon_idle_resume(
        "postgresql+psycopg://user:password@ep-one-pooler.aws.neon.tech/database",
        "secret-test-only",
        "project-one",
        "ep-one",
    )
    assert report["status"] == "PASS" and report["cold_start_verified"]
    assert report["quiet_seconds"] == clock[0] == 310
    assert report["resume_connection_ms"] == 250 and connection.call_count == 2
    assert "secret-test-only" not in json.dumps(report)
    assert "password" not in json.dumps(report)


def test_neon_idle_rejects_too_short_window_before_any_request():
    assert (
        probe.probe_neon_idle_resume("dsn", "secret", "project-one", "ep-one", 300)["status"]
        == "FAIL"
    )
