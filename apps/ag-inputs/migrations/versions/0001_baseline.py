"""Baseline: the fields, seasons, and field_events tables as originally created by create_all.

Databases that were created by the old `init_db()` (create_all) already have these tables.
Mark them as being at this revision instead of re-creating them:

    alembic stamp 0001
    alembic upgrade head

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fields",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("centroid_lat", sa.Float(), nullable=False),
        sa.Column("centroid_lon", sa.Float(), nullable=False),
        sa.Column("acres", sa.Float(), nullable=True),
    )
    op.create_table(
        "seasons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("field_id", sa.Integer(), sa.ForeignKey("fields.id"), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("crop", sa.String(20), nullable=False),
        sa.Column("variety", sa.String(120), nullable=True),
        sa.Column("planting_date", sa.Date(), nullable=True),
    )
    op.create_index("ix_seasons_field_id", "seasons", ["field_id"])
    op.create_table(
        "field_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("season_id", sa.Integer(), sa.ForeignKey("seasons.id"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_field_events_season_id", "field_events", ["season_id"])
    op.create_index("ix_field_events_kind", "field_events", ["kind"])
    op.create_index("ix_field_events_occurred_at", "field_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_table("field_events")
    op.drop_table("seasons")
    op.drop_table("fields")
