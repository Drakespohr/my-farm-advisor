"""Field NRD context: nrd, nrd_phase, soil_texture (all optional).

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("fields") as batch:
        batch.add_column(sa.Column("nrd", sa.String(40), nullable=True))
        batch.add_column(sa.Column("nrd_phase", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("soil_texture", sa.String(30), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("fields") as batch:
        batch.drop_column("soil_texture")
        batch.drop_column("nrd_phase")
        batch.drop_column("nrd")
