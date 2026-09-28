import httpx
import pytest

from product_pipeline.client import Client


def test_sdk_keeps_explicit_idempotency_and_etag():
    requests = []

    def reply(request):
        requests.append(request)
        return httpx.Response(202, json={"id": "job", "state": "queued"})

    with Client("https://example.test", "test-token") as client:
        client.http.close()
        client.http = httpx.Client(
            base_url="https://example.test", transport=httpx.MockTransport(reply)
        )
        assert client.post("/v1/runs", {"recipe_id": "r"}, key="reused", etag='"2"')["id"] == "job"
    assert requests[0].headers["Idempotency-Key"] == "reused"
    assert requests[0].headers["If-Match"] == '"2"'


def test_sdk_wait_returns_operator_pause_without_hanging():
    with Client("https://example.test", "test-token") as client:
        client.http.close()
        client.http = httpx.Client(
            base_url="https://example.test",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"id": "job", "state": "paused"})
            ),
        )
        assert client.wait("job")["state"] == "paused"
        with pytest.raises(TimeoutError):
            client.wait("job", timeout=0)
