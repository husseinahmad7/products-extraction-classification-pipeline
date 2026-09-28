import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from sqlalchemy import select

from product_pipeline.errors import PipelineError

from .db import AdminToken, audit, now, uid

HASHER = PasswordHasher()


def issue_token(session, workspace: str, actor: str, recover: bool = False) -> str:
    existing = session.scalars(select(AdminToken).where(AdminToken.revoked.is_(False))).all()
    if actor == "bootstrap" and existing and not recover:
        raise PipelineError("ALREADY_BOOTSTRAPPED", "use authenticated rotation or local recovery")
    if recover:
        for row in existing:
            row.revoked = True
    token_id, secret = uid(), secrets.token_urlsafe(32)
    session.add(AdminToken(id=token_id, secret_hash=HASHER.hash(secret)))
    audit(session, workspace, actor, "token.recovered" if recover else "token.created", token_id)
    return f"{token_id}.{secret}"


def authenticate(session, credential: str) -> str:
    try:
        token_id, secret = credential.split(".", 1)
        row = session.get(AdminToken, token_id)
        if row is None or row.revoked or (row.expires is not None and row.expires <= now()):
            raise ValueError()
        HASHER.verify(row.secret_hash, secret)
        return token_id
    except (ValueError, VerificationError) as exc:
        raise PipelineError("UNAUTHORIZED", "valid administrator token required") from exc


def rotate(session, workspace: str, actor: str, overlap_seconds: int = 300) -> str:
    old = session.get(AdminToken, actor)
    old.expires = now() + timedelta(seconds=overlap_seconds)
    return issue_token(session, workspace, actor)
