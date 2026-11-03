"""Add field_events.recorded_by (who made the record).

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("field_events") as batch:
        batch.add_column(sa.Column("recorded_by", sa.String(120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("field_events") as batch:
        batch.drop_column("recorded_by")
