"""Add versioned courseware plans and task linkage."""

from alembic import op
import sqlalchemy as sa


revision = "0005_courseware_plans"
down_revision = "0004_materials_and_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "courseware_plans",
        sa.Column("plan_id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("brief_id", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("source_refs", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["brief_id"], ["teaching_briefs.brief_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("plan_id"),
    )
    op.create_index("ix_courseware_plans_user_id", "courseware_plans", ["user_id"])
    op.create_index("ix_courseware_plans_project_id", "courseware_plans", ["project_id"])
    op.create_index("ix_courseware_plans_brief_id", "courseware_plans", ["brief_id"])
    op.create_index("ix_courseware_plans_status", "courseware_plans", ["status"])
    op.create_index(
        "ix_courseware_plans_project_version",
        "courseware_plans",
        ["project_id", "version"],
    )

    op.add_column("tasks", sa.Column("plan_id", sa.String(length=40), nullable=True))
    op.create_index("ix_tasks_plan_id", "tasks", ["plan_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_plan_id", table_name="tasks")
    op.drop_column("tasks", "plan_id")

    op.drop_index("ix_courseware_plans_project_version", table_name="courseware_plans")
    op.drop_index("ix_courseware_plans_status", table_name="courseware_plans")
    op.drop_index("ix_courseware_plans_brief_id", table_name="courseware_plans")
    op.drop_index("ix_courseware_plans_project_id", table_name="courseware_plans")
    op.drop_index("ix_courseware_plans_user_id", table_name="courseware_plans")
    op.drop_table("courseware_plans")
