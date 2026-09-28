"""Integration checks for a real S3-compatible object store.

Set PIPELINE_TEST_S3_ENDPOINT and PIPELINE_TEST_S3_BUCKET to run these tests.
The CI job supplies an isolated RustFS instance and disposable credentials.
"""

import os
from uuid import uuid4

import boto3
import pytest

from product_pipeline.errors import PipelineError
from product_pipeline.storage import S3Store

ENDPOINT = os.getenv("PIPELINE_TEST_S3_ENDPOINT")
BUCKET = os.getenv("PIPELINE_TEST_S3_BUCKET")
pytestmark = pytest.mark.skipif(
    not ENDPOINT or not BUCKET,
    reason="requires PIPELINE_TEST_S3_ENDPOINT and PIPELINE_TEST_S3_BUCKET",
)


@pytest.fixture
def store():
    assert ENDPOINT and BUCKET
    return S3Store(BUCKET, ENDPOINT)


def test_s3_round_trip_is_content_addressed_and_idempotent(store):
    payload = f"s3-integration-{uuid4()}".encode()
    key = store.put(payload)
    assert len(key) == 64
    assert store.get(key) == payload
    assert store.put(payload) == key
    assert store.get(key) == payload


def test_s3_missing_and_invalid_keys_fail_explicitly(store):
    with pytest.raises(PipelineError, match="absent|could not be read"):
        store.get("0" * 64)
    with pytest.raises(PipelineError, match="SHA-256"):
        store.get("../outside")


def test_s3_detects_tampered_object(store):
    assert BUCKET
    payload = f"tamper-integration-{uuid4()}".encode()
    key = store.put(payload)
    client = boto3.client("s3", endpoint_url=ENDPOINT)
    client.put_object(Bucket=BUCKET, Key=key, Body=b"tampered")
    with pytest.raises(PipelineError, match="digest mismatch"):
        store.get(key)
