"""Bound request bodies before parsing; no token or payload logging."""

from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from product_pipeline.errors import PipelineError


class BoundaryMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        artifact_limit: int,
        auth_url: str | None = None,
        connect_origins: tuple[str, ...] = (),
        public_beta: bool = False,
    ):
        self.app, self.artifact_limit = app, artifact_limit
        self.public_beta = public_beta
        candidates = list(connect_origins) + ([auth_url] if auth_url else [])
        self.connect = ["'self'"]
        for value in candidates:
            parsed = urlsplit(value)
            if (
                parsed.scheme == "https"
                and parsed.hostname
                and not parsed.username
                and not parsed.password
            ):
                self.connect.append(f"https://{parsed.netloc}")

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                        (
                            b"content-security-policy",
                            (
                                "default-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; connect-src "
                                + " ".join(self.connect)
                            ).encode("ascii"),
                        ),
                    ]
                )
                if scope["path"].startswith("/v1"):
                    headers = [
                        (name, value) for name, value in headers if name.lower() != b"cache-control"
                    ]
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        if scope["method"] in {"POST", "PUT", "PATCH"}:
            is_artifact = scope["path"] == "/v1/artifacts"
            limit = self.artifact_limit if is_artifact else 1_000_000
            if self.public_beta:
                limit = min(limit, 1_000_000 if is_artifact else 100_000)
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > limit:
                    response = JSONResponse(
                        PipelineError("REQUEST_TOO_LARGE", "request exceeds byte budget").problem(
                            413
                        ),
                        status_code=413,
                        media_type="application/problem+json",
                    )
                    await response(scope, receive, secure_send)
                    return
                if not message.get("more_body", False):
                    break
            if self.public_beta:
                # Conservative allowance for metadata, idempotency copies and audit/outbox.
                scope["pipeline.metadata_reservation"] = 8192 + (
                    0 if is_artifact else 4 * len(body)
                )
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, bounded_receive, secure_send)
        else:
            await self.app(scope, receive, secure_send)
