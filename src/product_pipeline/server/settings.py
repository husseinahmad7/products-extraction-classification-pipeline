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
    auth_mode: str = "admin"
    neon_auth_issuer: str | None = None
    neon_auth_jwks_url: str | None = None
    neon_auth_audience: str | None = None
    operator_subjects: tuple[str, ...] = ()
    cors_origins: tuple[str, ...] = ()
    beta_artifact_quota_bytes: int = 20_000_000
    beta_global_artifact_quota_bytes: int = 800_000_000
    beta_max_jobs: int = 30
    beta_global_max_jobs: int = 150
    beta_max_api_requests: int = 1000
    beta_global_max_api_requests: int = 10_000
    beta_global_max_runtime_seconds: int = 9000
    beta_metadata_quota_bytes: int = 5_000_000
    beta_global_metadata_quota_bytes: int = 80_000_000

    def __post_init__(self):
        from urllib.parse import urlsplit

        if self.auth_mode not in {"admin", "neon"}:
            raise ValueError("PIPELINE_AUTH_MODE must be admin or neon")
        if self.auth_mode == "neon":
            for value in (self.neon_auth_issuer, self.neon_auth_jwks_url):
                parsed = urlsplit(value or "")
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                ):
                    raise ValueError("Neon mode requires trusted HTTPS issuer and JWKS URLs")
        for budget_value in (
            self.beta_artifact_quota_bytes,
            self.beta_global_artifact_quota_bytes,
            self.beta_max_jobs,
            self.beta_global_max_jobs,
            self.beta_max_api_requests,
            self.beta_global_max_api_requests,
            self.beta_global_max_runtime_seconds,
            self.beta_metadata_quota_bytes,
            self.beta_global_metadata_quota_bytes,
        ):
            if budget_value < 1:
                raise ValueError("beta resource budgets must be positive")

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
            max_upload_bytes=int(os.getenv("PIPELINE_MAX_UPLOAD_BYTES", "10000000")),
            auth_mode=os.getenv("PIPELINE_AUTH_MODE", "admin"),
            neon_auth_issuer=os.getenv("NEON_AUTH_BASE_URL"),
            neon_auth_jwks_url=os.getenv("NEON_AUTH_JWKS_URL"),
            neon_auth_audience=os.getenv("NEON_AUTH_AUDIENCE"),
            operator_subjects=tuple(
                filter(None, os.getenv("PIPELINE_OPERATOR_SUBJECTS", "").split(","))
            ),
            cors_origins=tuple(filter(None, os.getenv("PIPELINE_CORS_ORIGINS", "").split(","))),
            beta_artifact_quota_bytes=int(
                os.getenv("PIPELINE_BETA_ARTIFACT_QUOTA_BYTES", "20000000")
            ),
            beta_global_artifact_quota_bytes=int(
                os.getenv("PIPELINE_BETA_GLOBAL_ARTIFACT_QUOTA_BYTES", "800000000")
            ),
            beta_max_jobs=int(os.getenv("PIPELINE_BETA_MAX_JOBS", "30")),
            beta_global_max_jobs=int(os.getenv("PIPELINE_BETA_GLOBAL_MAX_JOBS", "150")),
            beta_max_api_requests=int(os.getenv("PIPELINE_BETA_MAX_API_REQUESTS", "1000")),
            beta_global_max_api_requests=int(
                os.getenv("PIPELINE_BETA_GLOBAL_MAX_API_REQUESTS", "10000")
            ),
            beta_global_max_runtime_seconds=int(
                os.getenv("PIPELINE_BETA_GLOBAL_MAX_RUNTIME_SECONDS", "9000")
            ),
            beta_metadata_quota_bytes=int(
                os.getenv("PIPELINE_BETA_METADATA_QUOTA_BYTES", "5000000")
            ),
            beta_global_metadata_quota_bytes=int(
                os.getenv("PIPELINE_BETA_GLOBAL_METADATA_QUOTA_BYTES", "80000000")
            ),
        )

    def raw_store(self):
        raw = (
            S3Store(self.s3_bucket, self.s3_endpoint)
            if self.s3_bucket
            else LocalStore(Path(self.artifact_root))
        )
        return raw

    def store(self, database=None, workspace: str | None = None):
        raw = self.raw_store()
        if database is None:
            return raw
        from .artifacts import QuotaStore

        beta = self.auth_mode == "neon"
        return QuotaStore(
            raw,
            database,
            workspace or self.workspace,
            self.beta_artifact_quota_bytes if beta else self.artifact_quota_bytes,
            global_limit_bytes=self.beta_global_artifact_quota_bytes if beta else None,
            read_budget_settings=self if beta else None,
        )
