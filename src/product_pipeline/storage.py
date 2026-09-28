"""Content-addressed stores verify bytes on every read; no model artifacts."""

import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

from .errors import PipelineError
from .hashing import digest


def valid_key(key: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", key):
        raise PipelineError("ARTIFACT_KEY_INVALID", "artifact key must be a SHA-256 digest")
    return key


class ArtifactStore(Protocol):
    def put(self, data: bytes) -> str: ...
    def get(self, key: str) -> bytes: ...


class LocalStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes) -> str:
        key = digest(data)
        path = self.root / key
        staging = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.root, prefix=".stage-", delete=False
            ) as handle:
                staging = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(staging, path)
        except FileExistsError:
            self.get(key)
        finally:
            if staging:
                staging.unlink(missing_ok=True)
        return key

    def get(self, key: str) -> bytes:
        try:
            data = (self.root / valid_key(key)).read_bytes()
        except FileNotFoundError as exc:
            raise PipelineError("REPLAY_UNAVAILABLE", "required artifact is absent") from exc
        if digest(data) != key:
            raise PipelineError("ARTIFACT_CORRUPT", "artifact digest mismatch")
        return data


class S3Store:
    def __init__(self, bucket: str, endpoint: str | None = None):
        import boto3

        self.client = boto3.client("s3", endpoint_url=endpoint)
        self.bucket = bucket

    def put(self, data: bytes) -> str:
        key = digest(data)
        from botocore.exceptions import ClientError

        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                IfNoneMatch="*",
                ServerSideEncryption="AES256",
            )
        except ClientError as exc:
            if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                raise
            self.get(key)
        return key

    def get(self, key: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            response = self.client.get_object(Bucket=self.bucket, Key=valid_key(key))
            data = response["Body"].read()
        except ClientError as exc:
            raise PipelineError(
                "REPLAY_UNAVAILABLE", "required artifact could not be read"
            ) from exc
        if digest(data) != key:
            raise PipelineError("ARTIFACT_CORRUPT", "artifact digest mismatch")
        return data
