"""Bound request bodies before parsing; no token or payload logging."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from product_pipeline.errors import PipelineError


class BoundaryMiddleware:
    def __init__(self, app: ASGIApp, artifact_limit: int):
        self.app, self.artifact_limit = app, artifact_limit

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
                            b"default-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
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
            limit = self.artifact_limit if scope["path"] == "/v1/artifacts" else 1_000_000
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
