"""Persist TeachingBrief versions, intent state and SSE event payloads."""

from alembic import op
import sqlalchemy as sa


revision = "0003_teaching_briefs"
down_revision = "0002_users_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teaching_briefs",
        sa.Column("brief_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("source_refs", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("brief_id"),
    )
    op.create_index("ix_teaching_briefs_user_id", "teaching_briefs", ["user_id"], unique=False)
    op.create_index("ix_teaching_briefs_project_id", "teaching_briefs", ["project_id"], unique=False)
    op.create_index("ix_teaching_briefs_session_id", "teaching_briefs", ["session_id"], unique=False)

    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(sa.Column("intent_state", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("intent_data", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("current_brief_id", sa.String(), nullable=True))

    with op.batch_alter_table("chat_messages") as batch_op:
        batch_op.add_column(sa.Column("event_data", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chat_messages") as batch_op:
        batch_op.drop_column("event_data")

    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_column("current_brief_id")
        batch_op.drop_column("intent_data")
        batch_op.drop_column("intent_state")

    op.drop_index("ix_teaching_briefs_session_id", table_name="teaching_briefs")
    op.drop_index("ix_teaching_briefs_project_id", table_name="teaching_briefs")
    op.drop_index("ix_teaching_briefs_user_id", table_name="teaching_briefs")
    op.drop_table("teaching_briefs")
