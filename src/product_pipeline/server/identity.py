"""Trusted identity -> personal workspace. No tenant identity comes from request data."""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass

import httpx
import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from product_pipeline.errors import PipelineError
from product_pipeline.hashing import object_hash
from product_pipeline.storage import ArtifactStore

from .artifacts import QuotaStore
from .auth import authenticate
from .db import UserWorkspace


@dataclass(frozen=True)
class Identity:
    actor: str
    workspace: str
    is_operator: bool
    auth_mode: str
    store: ArtifactStore


CURRENT: ContextVar[Identity] = ContextVar("pipeline_identity")


def identity() -> Identity:
    return CURRENT.get()


def workspace() -> str:
    return identity().workspace


def operator():
    if not identity().is_operator:
        raise PipelineError("FORBIDDEN", "platform operator permission required")


class ScopedStore:
    def put(self, data: bytes) -> str:
        return identity().store.put(data)

    def get(self, key: str) -> bytes:
        return identity().store.get(key)


class JWKSVerifier:
    """Fixed Ed25519 trust root; unknown key IDs cannot force repeated network fetches."""

    def __init__(self, issuer: str, jwks_url: str, audience: str | None = None):
        self.issuer, self.url, self.audience = issuer, jwks_url, audience
        self.keys: dict = {}
        self.fetched = float("-inf")
        self.lock = threading.Lock()

    def __call__(self, token: str) -> dict:
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if header.get("alg") != "EdDSA" or not isinstance(kid, str) or not 1 <= len(kid) <= 128:
                raise ValueError("invalid signing header")
            with self.lock:
                age = time.monotonic() - self.fetched
                if age > 300 or (kid not in self.keys and age >= 60):
                    self.fetched = time.monotonic()
                    with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
                        response = client.get(self.url)
                        response.raise_for_status()
                        if len(response.content) > 100_000:
                            raise ValueError("JWKS too large")
                        rows = response.json().get("keys", [])
                    if not isinstance(rows, list) or len(rows) > 100:
                        raise ValueError("invalid JWKS")
                    if any(not isinstance(row, dict) for row in rows):
                        raise ValueError("invalid JWKS entries")
                    self.keys = {
                        row["kid"]: jwt.PyJWK.from_dict(row).key
                        for row in rows
                        if row.get("kty") == "OKP"
                        and row.get("crv") == "Ed25519"
                        and row.get("use", "sig") == "sig"
                        and row.get("alg", "EdDSA") == "EdDSA"
                    }
                key = self.keys.get(kid)
            if key is None:
                raise ValueError("unknown signing key")
            claims = jwt.decode(
                token,
                key,
                algorithms=["EdDSA"],
                issuer=self.issuer,
                audience=self.audience,
                leeway=15,
                options={
                    "require": ["iss", "sub", "iat", "exp"],
                    "verify_aud": self.audience is not None,
                },
            )
            if claims.get("role") in {"anonymous", "anon"}:
                raise ValueError("anonymous identities unsupported")
            if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 512:
                raise ValueError("invalid subject")
            if claims["exp"] - claims["iat"] > 1800:
                raise ValueError("unexpected token lifetime")
            return claims
        except (jwt.PyJWTError, ValueError, KeyError, TypeError, httpx.HTTPError) as exc:
            raise PipelineError("UNAUTHORIZED", "valid Neon user JWT required") from exc


class IdentityMiddleware:
    def __init__(self, app, database, settings, raw_store, verifier=None):
        self.app, self.db, self.settings, self.raw = app, database, settings, raw_store
        self.verifier = verifier
        if settings.auth_mode == "neon" and verifier is None:
            self.verifier = JWKSVerifier(
                settings.neon_auth_issuer, settings.neon_auth_jwks_url, settings.neon_auth_audience
            )

    def resolve(self, credential: str, metadata_bytes: int = 0) -> Identity:
        config = self.settings
        if config.auth_mode == "admin":
            with self.db.sessions() as session:
                who = authenticate(session, credential)
            store = (
                self.raw
                if isinstance(self.raw, QuotaStore)
                else QuotaStore(self.raw, self.db, config.workspace, config.artifact_quota_bytes)
            )
            return Identity(who, config.workspace, True, "admin", store)
        claims = self.verifier(credential)
        # Verifiers are constructor-injected only in tests, never configurable by an HTTP caller.
        subject = claims["sub"]
        tenant = "u_" + object_hash({"issuer": config.neon_auth_issuer, "subject": subject})
        from .public_beta import reserve_api

        with self.db.sessions.begin() as session:
            account_charge = 8192 if session.get(UserWorkspace, tenant) is None else 0
            reserve_api(session, tenant, config, metadata_bytes + account_charge)
        try:
            with self.db.sessions.begin() as session:
                if session.get(UserWorkspace, tenant) is None:
                    session.add(
                        UserWorkspace(
                            workspace=tenant, issuer=config.neon_auth_issuer, subject=subject
                        )
                    )
        except IntegrityError:
            with self.db.sessions() as session:
                if session.get(UserWorkspace, tenant) is None:
                    raise
        raw = self.raw.store if isinstance(self.raw, QuotaStore) else self.raw
        store = QuotaStore(
            raw,
            self.db,
            tenant,
            config.beta_artifact_quota_bytes,
            global_limit_bytes=config.beta_global_artifact_quota_bytes,
            read_budget_settings=config,
        )
        return Identity(
            "user:" + tenant[2:], tenant, subject in config.operator_subjects, "neon", store
        )

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or not scope["path"].startswith("/v1/")
            or scope["method"] == "OPTIONS"
        ):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        value = headers.get(b"authorization", b"")
        try:
            if not value.startswith(b"Bearer ") or len(value) > 8192:
                raise PipelineError("UNAUTHORIZED", "bearer authentication required")
            who = await run_in_threadpool(
                self.resolve,
                value[7:].decode("ascii"),
                scope.get("pipeline.metadata_reservation", 0),
            )
        except (PipelineError, UnicodeError, StaleDataError) as exc:
            if isinstance(exc, StaleDataError):
                response = JSONResponse(
                    PipelineError("CONFLICT", "concurrent admission; retry request").problem(409),
                    status_code=409,
                    media_type="application/problem+json",
                )
                await response(scope, receive, send)
                return
            if not isinstance(exc, PipelineError):
                exc = PipelineError("UNAUTHORIZED", "invalid bearer credential")
            status = 507 if exc.code == "QUOTA_EXCEEDED" else 401
            response = JSONResponse(
                exc.problem(status),
                status_code=status,
                media_type="application/problem+json",
                headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
            )
            await response(scope, receive, send)
            return
        token = CURRENT.set(who)
        try:
            await self.app(scope, receive, send)
        finally:
            CURRENT.reset(token)
