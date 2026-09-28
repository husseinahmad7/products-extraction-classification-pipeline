"""Evidence foundation. Frozen schema; do not import evolving application models."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "resources",
        sa.Column("workspace", sa.String(128), primary_key=True),
        sa.Column("kind", sa.String(40), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_resources_created", "resources", ["created"])
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("owner", sa.String(128), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("available", sa.DateTime(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
    )
    for name in ["workspace", "state", "available"]:
        op.create_index("ix_jobs_" + name, "jobs", [name])
    op.create_table(
        "idempotency",
        sa.Column("workspace", sa.String(128), primary_key=True),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
    )
    op.create_table(
        "audit",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("target", sa.String(128), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_audit_workspace", "audit", ["workspace"])
    op.create_table(
        "outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("event", sa.JSON(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_outbox_workspace", "outbox", ["workspace"])
    op.create_table(
        "dataset_heads",
        sa.Column("workspace", sa.String(128), primary_key=True),
        sa.Column("source_id", sa.String(128), primary_key=True),
        sa.Column("revision_id", sa.String(36), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "admin_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("secret_hash", sa.String(512), nullable=False, unique=True),
        sa.Column("expires", sa.DateTime(), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "storage_quotas",
        sa.Column("workspace", sa.String(128), primary_key=True),
        sa.Column("limit_bytes", sa.BigInteger(), nullable=False),
        sa.Column("used_bytes", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "artifacts",
        sa.Column("workspace", sa.String(128), primary_key=True),
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'audit events are append-only'; END; $$""")
        op.execute("""CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit
            FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()""")
        op.execute("""CREATE FUNCTION reject_revision_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF OLD.kind IN ('revision', 'schema', 'taxonomy') THEN
                RAISE EXCEPTION 'immutable resource cannot be changed';
              END IF;
              IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
              RETURN NEW;
            END; $$""")
        op.execute("""CREATE TRIGGER resources_immutable BEFORE UPDATE OR DELETE ON resources
            FOR EACH ROW EXECUTE FUNCTION reject_revision_mutation()""")


def downgrade():
    raise RuntimeError(
        "Destructive downgrade is deliberately unsupported. Restore a verified backup to a separate database."
    )
