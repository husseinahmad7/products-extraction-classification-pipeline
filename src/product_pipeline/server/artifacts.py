"""Durable quota reservations; preserve evidence and pause instead of evicting."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from product_pipeline.errors import PipelineError
from product_pipeline.hashing import digest

from .db import Artifact, Database, StorageQuota


class QuotaStore:
    def __init__(self, store, database: Database, workspace: str, limit_bytes: int):
        if limit_bytes < 1:
            raise ValueError("artifact quota must be positive")
        self.store, self.db, self.workspace = store, database, workspace
        try:
            with database.sessions.begin() as session:
                if session.get(StorageQuota, workspace) is None:
                    session.add(
                        StorageQuota(workspace=workspace, limit_bytes=limit_bytes, used_bytes=0)
                    )
        except IntegrityError:
            # Another process initialized the same workspace.
            pass

    def put(self, data: bytes) -> str:
        key = digest(data)
        with self.db.sessions.begin() as session:
            quota = session.scalar(
                select(StorageQuota)
                .where(StorageQuota.workspace == self.workspace)
                .with_for_update()
            )
            if quota is None:
                raise PipelineError("STATE_CORRUPT", "artifact quota is missing")
            existing = session.get(Artifact, (self.workspace, key))
            if existing is None:
                if quota.used_bytes + len(data) > quota.limit_bytes:
                    raise PipelineError(
                        "QUOTA_EXCEEDED",
                        "artifact quota reached; expand capacity before resuming. No evidence was evicted.",
                    )
                quota.used_bytes += len(data)
                session.add(
                    Artifact(workspace=self.workspace, digest=key, size=len(data), state="reserved")
                )
        # Reservation survives upload failure or worker death. A retry reuses it.
        actual = self.store.put(data)
        if actual != key:
            raise PipelineError("ARTIFACT_CORRUPT", "artifact store returned a different digest")
        with self.db.sessions.begin() as session:
            row = session.get(Artifact, (self.workspace, key))
            if row is None:
                raise PipelineError("STATE_CORRUPT", "artifact reservation is missing")
            row.state = "stored"
        return key

    def get(self, key: str) -> bytes:
        with self.db.sessions() as session:
            row = session.get(Artifact, (self.workspace, key))
            if row is None or row.state != "stored":
                raise PipelineError("NOT_FOUND", "artifact has not been retained in this workspace")
        return self.store.get(key)
