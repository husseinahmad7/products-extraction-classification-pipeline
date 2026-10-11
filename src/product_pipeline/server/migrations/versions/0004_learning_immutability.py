"""Enforce retained learning version immutability in PostgreSQL itself."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE OR REPLACE FUNCTION reject_revision_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF OLD.kind IN ('revision', 'schema', 'taxonomy', 'label', 'dataset', 'model') THEN
                RAISE EXCEPTION 'immutable resource cannot be changed';
              END IF;
              IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
              RETURN NEW;
            END; $$""")


def downgrade():
    raise RuntimeError("Destructive downgrade unsupported. Restore a verified backup.")
