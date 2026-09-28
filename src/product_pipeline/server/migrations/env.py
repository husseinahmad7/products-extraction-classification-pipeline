from alembic import context
from sqlalchemy import create_engine, pool

from product_pipeline.server.db import Base
from product_pipeline.server.settings import Settings

config = context.config
target_metadata = Base.metadata
url = config.attributes.get("database_url") or Settings.from_env().database_url

if context.is_offline_mode():
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
