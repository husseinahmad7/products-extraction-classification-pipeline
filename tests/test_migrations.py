from pathlib import Path

from alembic import command
from alembic.config import Config

from product_pipeline import server
from product_pipeline.server.db import Database


def test_upgrade_is_repeatable_and_matches_metadata(tmp_path):
    config = Config()
    config.set_main_option("script_location", str(Path(server.__file__).parent / "migrations"))
    config.attributes["database_url"] = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    command.check(config)
    db = Database(config.attributes["database_url"])
    assert db.claim("empty") is None
    db.engine.dispose()
