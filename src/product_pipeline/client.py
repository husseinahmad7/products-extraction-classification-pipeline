"""Small synchronous SDK; no server or model dependencies are imported."""

import time
from uuid import uuid4

import httpx


class Client:
    def __init__(self, base_url: str, token: str, timeout: float = 30):
        self.http = httpx.Client(
            base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=timeout
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.http.close()

    def get(self, path: str, **params):
        response = self.http.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def post(
        self, path: str, body: dict | None = None, key: str | None = None, etag: str | None = None
    ):
        headers = {"Idempotency-Key": key or str(uuid4())}
        if etag:
            headers["If-Match"] = etag
        response = self.http.post(path, json=body or {}, headers=headers)
        response.raise_for_status()
        return response.json()

    def wait(self, operation_id: str, timeout: float = 300, interval: float = 1):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.get(f"/v1/operations/{operation_id}")
            if result["state"] in {"succeeded", "failed", "cancelled", "paused"}:
                return result
            time.sleep(interval)
        raise TimeoutError(f"operation {operation_id} is still pending")
