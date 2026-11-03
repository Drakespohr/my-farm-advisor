"""Multi-user access: users, farms, memberships, drafts; fields.farm_id; event corrections.

Existing rows are kept as they are:
- fields get farm_id = NULL until assigned to a farm (python manage.py assign-fields ...);
- field_events get supersedes_id / entered_by_user_id = NULL.

field_events becomes append-only at the database level (UPDATE and DELETE raise).
NOTE for future migrations: on SQLite, batch-altering field_events recreates the table and
drops these triggers; call _create_append_only_triggers() again afterwards.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

APPEND_ONLY_MSG = "field_events is append-only; record a correction with supersedes_id"


def _create_append_only_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(f"""
            CREATE OR REPLACE FUNCTION field_events_append_only() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION '{APPEND_ONLY_MSG}'; END;
            $$ LANGUAGE plpgsql""")
        op.execute("""CREATE TRIGGER field_events_append_only BEFORE UPDATE OR DELETE ON field_events
                      FOR EACH ROW EXECUTE FUNCTION field_events_append_only()""")
    elif dialect == "sqlite":
        op.execute(f"CREATE TRIGGER field_events_no_update BEFORE UPDATE ON field_events "
                   f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY_MSG}'); END")
        op.execute(f"CREATE TRIGGER field_events_no_delete BEFORE DELETE ON field_events "
                   f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY_MSG}'); END")


def _drop_append_only_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS field_events_append_only ON field_events")
        op.execute("DROP FUNCTION IF EXISTS field_events_append_only()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS field_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS field_events_no_delete")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("name", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_users_subject", "users", ["subject"], unique=True)
    op.create_index("ix_users_email", "users", ["email"])
    op.create_table(
        "farms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("farm_id", sa.Integer(), sa.ForeignKey("farms.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("farm_id", "user_id", name="uq_membership_farm_user"),
        sa.CheckConstraint("role IN ('owner','operator','applicator','scout','viewer')", name="ck_membership_role"),
    )
    op.create_index("ix_memberships_farm_id", "memberships", ["farm_id"])
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    op.create_table(
        "drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("season_id", sa.Integer(), sa.ForeignKey("seasons.id"), nullable=False),
        sa.Column("form_type", sa.String(40), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "form_type", "season_id", name="uq_draft_user_form_season"),
    )
    op.create_index("ix_drafts_user_id", "drafts", ["user_id"])
    op.create_index("ix_drafts_season_id", "drafts", ["season_id"])

    with op.batch_alter_table("fields") as batch:
        batch.add_column(sa.Column("farm_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_fields_farm_id", "farms", ["farm_id"], ["id"])
        batch.create_index("ix_fields_farm_id", ["farm_id"])
    with op.batch_alter_table("field_events") as batch:
        batch.add_column(sa.Column("supersedes_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("entered_by_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_field_events_supersedes_id", "field_events", ["supersedes_id"], ["id"])
        batch.create_foreign_key("fk_field_events_entered_by", "users", ["entered_by_user_id"], ["id"])
        batch.create_index("ix_field_events_supersedes_id", ["supersedes_id"])
    _create_append_only_triggers()


def downgrade() -> None:
    _drop_append_only_triggers()
    with op.batch_alter_table("field_events") as batch:
        batch.drop_index("ix_field_events_supersedes_id")
        batch.drop_constraint("fk_field_events_entered_by", type_="foreignkey")
        batch.drop_constraint("fk_field_events_supersedes_id", type_="foreignkey")
        batch.drop_column("entered_by_user_id")
        batch.drop_column("supersedes_id")
    with op.batch_alter_table("fields") as batch:
        batch.drop_index("ix_fields_farm_id")
        batch.drop_constraint("fk_fields_farm_id", type_="foreignkey")
        batch.drop_column("farm_id")
    op.drop_table("drafts")
    op.drop_table("memberships")
    op.drop_table("farms")
    op.drop_table("users")
