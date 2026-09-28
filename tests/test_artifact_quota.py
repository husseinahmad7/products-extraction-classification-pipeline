import pytest

from product_pipeline.errors import PipelineError
from product_pipeline.server.artifacts import QuotaStore
from product_pipeline.server.db import Database, StorageQuota
from product_pipeline.storage import LocalStore


def test_quota_preserves_existing_and_deduplicates(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'quota.db'}")
    db.initialize()
    store = QuotaStore(LocalStore(tmp_path / "artifacts"), db, "workspace", 4)
    key = store.put(b"test")
    assert store.put(b"test") == key
    with pytest.raises(PipelineError, match="No evidence was evicted"):
        store.put(b"extra")
    assert store.get(key) == b"test"
    with db.sessions() as session:
        assert session.get(StorageQuota, "workspace").used_bytes == 4
    db.engine.dispose()


def test_failed_write_keeps_reservation(tmp_path):
    class Broken:
        def put(self, data):
            raise OSError("unavailable")

    db = Database(f"sqlite:///{tmp_path / 'quota.db'}")
    db.initialize()
    store = QuotaStore(Broken(), db, "workspace", 4)
    with pytest.raises(OSError):
        store.put(b"test")
    with pytest.raises(PipelineError, match="quota reached"):
        store.put(b"other")
    store.store = LocalStore(tmp_path / "artifacts")
    assert store.get(store.put(b"test")) == b"test"
    db.engine.dispose()
