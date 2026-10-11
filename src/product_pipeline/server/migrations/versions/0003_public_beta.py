"""Personal workspaces, public-source approvals and durable monthly budgets."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_workspaces",
        sa.Column("workspace", sa.String(128), primary_key=True),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(512), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("issuer", "subject"),
    )
    op.create_table(
        "approved_domains",
        sa.Column("domain", sa.String(253), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "domain_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("decision_reason", sa.String(2000), nullable=True),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_domain_requests_workspace", "domain_requests", ["workspace"])
    op.create_table(
        "budget_usage",
        sa.Column("scope", sa.String(128), primary_key=True),
        sa.Column("period", sa.String(7), primary_key=True),
        sa.Column("counters", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    )


def downgrade():
    raise RuntimeError("Destructive downgrade unsupported. Restore a verified backup.")
