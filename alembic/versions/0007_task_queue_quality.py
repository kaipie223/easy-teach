"""Add durable queue metadata and artifact quality reports."""

from alembic import op
import sqlalchemy as sa


revision = "0007_task_queue_quality"
down_revision = "0006_versioning_and_exports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("celery_task_id", sa.String(length=255), nullable=True))
    op.add_column("tasks", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.add_column("tasks", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("error_code", sa.String(length=128), nullable=True))
    op.create_index("ix_tasks_celery_task_id", "tasks", ["celery_task_id"])
    op.create_index(
        "ix_tasks_idempotency_key", "tasks", ["user_id", "idempotency_key"], unique=True
    )
    op.create_index("ix_tasks_status_heartbeat", "tasks", ["status", "heartbeat_at"])

    op.add_column("exports", sa.Column("celery_task_id", sa.String(length=255), nullable=True))
    op.add_column("exports", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("exports", sa.Column("max_retries", sa.Integer(), nullable=False, server_default="2"))
    op.add_column("exports", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("exports", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_exports_celery_task_id", "exports", ["celery_task_id"])
    op.create_index("ix_exports_status_updated", "exports", ["status", "updated_at"])

    op.add_column(
        "artifact_versions",
        sa.Column("quality_status", sa.String(length=32), nullable=False, server_default="pending"),
    )
    op.add_column("artifact_versions", sa.Column("quality_report", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("artifact_versions", "quality_report")
    op.drop_column("artifact_versions", "quality_status")

    op.drop_index("ix_exports_status_updated", table_name="exports")
    op.drop_index("ix_exports_celery_task_id", table_name="exports")
    op.drop_column("exports", "updated_at")
    op.drop_column("exports", "started_at")
    op.drop_column("exports", "max_retries")
    op.drop_column("exports", "retry_count")
    op.drop_column("exports", "celery_task_id")

    op.drop_index("ix_tasks_status_heartbeat", table_name="tasks")
    op.drop_index("ix_tasks_idempotency_key", table_name="tasks")
    op.drop_index("ix_tasks_celery_task_id", table_name="tasks")
    op.drop_column("tasks", "error_code")
    op.drop_column("tasks", "completed_at")
    op.drop_column("tasks", "heartbeat_at")
    op.drop_column("tasks", "updated_at")
    op.drop_column("tasks", "started_at")
    op.drop_column("tasks", "idempotency_key")
    op.drop_column("tasks", "celery_task_id")
