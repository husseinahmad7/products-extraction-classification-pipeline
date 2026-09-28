"""Atomic per-origin request reservations."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "jobs",
        sa.Column(
            "created", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
    )
    op.create_table(
        "origin_throttles",
        sa.Column("origin", sa.String(512), primary_key=True),
        sa.Column("next_allowed", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "schedules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("recipe_id", sa.String(128), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("next_due", sa.DateTime(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_run_id", sa.String(36), nullable=True),
        sa.Column("created", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_schedules_workspace", "schedules", ["workspace"])
    op.create_index("ix_schedules_next_due", "schedules", ["next_due"])


def downgrade():
    raise RuntimeError(
        "Destructive downgrade is deliberately unsupported. Restore a verified backup."
    )
