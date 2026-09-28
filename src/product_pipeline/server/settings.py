import os
from dataclasses import dataclass
from pathlib import Path

from product_pipeline.storage import LocalStore, S3Store


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///pipeline.db"
    artifact_root: str = ".artifacts"
    s3_bucket: str | None = None
    s3_endpoint: str | None = None
    workspace: str = "default"
    max_upload_bytes: int = 10_000_000
    artifact_quota_bytes: int = 2_000_000_000
    dashboard_dir: str | None = None

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.getenv("PIPELINE_DATABASE_URL", "sqlite:///pipeline.db"),
            artifact_root=os.getenv("PIPELINE_ARTIFACT_ROOT", ".artifacts"),
            s3_bucket=os.getenv("PIPELINE_S3_BUCKET"),
            s3_endpoint=os.getenv("PIPELINE_S3_ENDPOINT"),
            workspace=os.getenv("PIPELINE_WORKSPACE", "default"),
            artifact_quota_bytes=int(os.getenv("PIPELINE_ARTIFACT_QUOTA_BYTES", "2000000000")),
            dashboard_dir=os.getenv("PIPELINE_DASHBOARD_DIR"),
        )

    def store(self, database=None):
        raw = (
            S3Store(self.s3_bucket, self.s3_endpoint)
            if self.s3_bucket
            else LocalStore(Path(self.artifact_root))
        )
        if database is None:
            return raw
        from .artifacts import QuotaStore

        return QuotaStore(raw, database, self.workspace, self.artifact_quota_bytes)
