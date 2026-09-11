"""Add immutable artifact versions, revision patches and export records."""

from alembic import op
import sqlalchemy as sa


revision = "0006_versioning_and_exports"
down_revision = "0005_courseware_plans"
branch_labels = None
depends_on = None


def _is_sqlite() -> bool:
    return op.get_context().dialect.name == "sqlite"


def upgrade() -> None:
    op.create_table(
        "artifact_versions",
        sa.Column("artifact_version_id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("source_plan_id", sa.String(length=40), nullable=False),
        sa.Column("base_version_id", sa.String(length=40), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_plan_id"], ["courseware_plans.plan_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["base_version_id"], ["artifact_versions.artifact_version_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("artifact_version_id"),
    )
    op.create_index("ix_artifact_versions_user_id", "artifact_versions", ["user_id"])
    op.create_index("ix_artifact_versions_project_id", "artifact_versions", ["project_id"])
    op.create_index("ix_artifact_versions_source_plan_id", "artifact_versions", ["source_plan_id"])
    op.create_index("ix_artifact_versions_base_version_id", "artifact_versions", ["base_version_id"])
    op.create_index("ix_artifact_versions_status", "artifact_versions", ["status"])
    op.create_index(
        "ix_artifact_versions_project_version", "artifact_versions", ["project_id", "version"]
    )

    op.create_table(
        "revision_patches",
        sa.Column("patch_id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("base_version_id", sa.String(length=40), nullable=False),
        sa.Column("created_version_id", sa.String(length=40), nullable=True),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("target_ids", sa.JSON(), nullable=False),
        sa.Column("operations_json", sa.JSON(), nullable=False),
        sa.Column("cascade_check", sa.JSON(), nullable=False),
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["base_version_id"], ["artifact_versions.artifact_version_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_version_id"], ["artifact_versions.artifact_version_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("patch_id"),
    )
    op.create_index("ix_revision_patches_user_id", "revision_patches", ["user_id"])
    op.create_index("ix_revision_patches_project_id", "revision_patches", ["project_id"])
    op.create_index("ix_revision_patches_base_version_id", "revision_patches", ["base_version_id"])
    op.create_index("ix_revision_patches_created_version_id", "revision_patches", ["created_version_id"])
    op.create_index("ix_revision_patches_status", "revision_patches", ["status"])
    op.create_index(
        "ix_revision_patches_project_status", "revision_patches", ["project_id", "status"]
    )

    op.create_table(
        "exports",
        sa.Column("export_id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("artifact_version_id", sa.String(length=40), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("file_id", sa.String(length=40), nullable=True),
        sa.Column("path", sa.String(length=1024), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["artifact_version_id"], ["artifact_versions.artifact_version_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["file_id"], ["files.file_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("export_id"),
    )
    op.create_index("ix_exports_user_id", "exports", ["user_id"])
    op.create_index("ix_exports_project_id", "exports", ["project_id"])
    op.create_index("ix_exports_artifact_version_id", "exports", ["artifact_version_id"])
    op.create_index("ix_exports_status", "exports", ["status"])
    op.create_index("ix_exports_version_format", "exports", ["artifact_version_id", "format"])

    op.add_column("tasks", sa.Column("artifact_version_id", sa.String(length=40), nullable=True))
    op.add_column("tasks", sa.Column("task_type", sa.String(length=32), nullable=True))
    op.add_column("tasks", sa.Column("retry_count", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("max_retries", sa.Integer(), nullable=True))
    op.execute("UPDATE tasks SET task_type = 'generation' WHERE task_type IS NULL")
    op.execute("UPDATE tasks SET retry_count = 0 WHERE retry_count IS NULL")
    op.execute("UPDATE tasks SET max_retries = 2 WHERE max_retries IS NULL")
    if _is_sqlite():
        with op.batch_alter_table("tasks") as batch_op:
            batch_op.create_foreign_key(
                "fk_tasks_artifact_version_id",
                "artifact_versions",
                ["artifact_version_id"],
                ["artifact_version_id"],
                ondelete="SET NULL",
            )
            batch_op.alter_column(
                "task_type",
                existing_type=sa.String(length=32),
                nullable=False,
            )
            batch_op.alter_column("retry_count", existing_type=sa.Integer(), nullable=False)
            batch_op.alter_column("max_retries", existing_type=sa.Integer(), nullable=False)
    else:
        op.create_foreign_key(
            "fk_tasks_artifact_version_id",
            "tasks",
            "artifact_versions",
            ["artifact_version_id"],
            ["artifact_version_id"],
            ondelete="SET NULL",
        )
        op.alter_column("tasks", "task_type", nullable=False)
        op.alter_column("tasks", "retry_count", nullable=False)
        op.alter_column("tasks", "max_retries", nullable=False)
    op.create_index("ix_tasks_artifact_version_id", "tasks", ["artifact_version_id"])

    op.add_column("files", sa.Column("artifact_version_id", sa.String(length=40), nullable=True))
    if _is_sqlite():
        with op.batch_alter_table("files") as batch_op:
            batch_op.create_foreign_key(
                "fk_files_artifact_version_id",
                "artifact_versions",
                ["artifact_version_id"],
                ["artifact_version_id"],
                ondelete="SET NULL",
            )
    else:
        op.create_foreign_key(
            "fk_files_artifact_version_id",
            "files",
            "artifact_versions",
            ["artifact_version_id"],
            ["artifact_version_id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_files_artifact_version_id", "files", ["artifact_version_id"])


def downgrade() -> None:
    op.drop_index("ix_files_artifact_version_id", table_name="files")
    if _is_sqlite():
        with op.batch_alter_table("files") as batch_op:
            batch_op.drop_constraint("fk_files_artifact_version_id", type_="foreignkey")
            batch_op.drop_column("artifact_version_id")
    else:
        op.drop_constraint("fk_files_artifact_version_id", "files", type_="foreignkey")
        op.drop_column("files", "artifact_version_id")

    op.drop_index("ix_tasks_artifact_version_id", table_name="tasks")
    if _is_sqlite():
        with op.batch_alter_table("tasks") as batch_op:
            batch_op.drop_constraint("fk_tasks_artifact_version_id", type_="foreignkey")
            batch_op.drop_column("max_retries")
            batch_op.drop_column("retry_count")
            batch_op.drop_column("task_type")
            batch_op.drop_column("artifact_version_id")
    else:
        op.drop_constraint("fk_tasks_artifact_version_id", "tasks", type_="foreignkey")
        op.drop_column("tasks", "max_retries")
        op.drop_column("tasks", "retry_count")
        op.drop_column("tasks", "task_type")
        op.drop_column("tasks", "artifact_version_id")

    op.drop_index("ix_exports_version_format", table_name="exports")
    op.drop_index("ix_exports_status", table_name="exports")
    op.drop_index("ix_exports_artifact_version_id", table_name="exports")
    op.drop_index("ix_exports_project_id", table_name="exports")
    op.drop_index("ix_exports_user_id", table_name="exports")
    op.drop_table("exports")

    op.drop_index("ix_revision_patches_project_status", table_name="revision_patches")
    op.drop_index("ix_revision_patches_status", table_name="revision_patches")
    op.drop_index("ix_revision_patches_created_version_id", table_name="revision_patches")
    op.drop_index("ix_revision_patches_base_version_id", table_name="revision_patches")
    op.drop_index("ix_revision_patches_project_id", table_name="revision_patches")
    op.drop_index("ix_revision_patches_user_id", table_name="revision_patches")
    op.drop_table("revision_patches")

    op.drop_index("ix_artifact_versions_project_version", table_name="artifact_versions")
    op.drop_index("ix_artifact_versions_status", table_name="artifact_versions")
    op.drop_index("ix_artifact_versions_base_version_id", table_name="artifact_versions")
    op.drop_index("ix_artifact_versions_source_plan_id", table_name="artifact_versions")
    op.drop_index("ix_artifact_versions_project_id", table_name="artifact_versions")
    op.drop_index("ix_artifact_versions_user_id", table_name="artifact_versions")
    op.drop_table("artifact_versions")
