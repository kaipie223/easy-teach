"""Enforce project-scoped version uniqueness.

Revision ID: 0010_project_version_constraints
Revises: 0009_private_teacher_knowledge
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_project_version_constraints"
down_revision = "0009_private_teacher_knowledge"
branch_labels = None
depends_on = None


def _renumber(table: str, primary_key: str, *, fallback_scope: str | None = None) -> None:
    connection = op.get_bind()
    scope_sql = "project_id"
    if fallback_scope:
        scope_sql = f"COALESCE(project_id, {fallback_scope})"
    rows = connection.execute(
        sa.text(
            f"SELECT {primary_key} AS row_id, {scope_sql} AS scope_id "
            f"FROM {table} WHERE {scope_sql} IS NOT NULL "
            f"ORDER BY scope_id, version, created_at, {primary_key}"
        )
    ).mappings()
    counters: dict[str, int] = {}
    for row in rows:
        scope_id = str(row["scope_id"])
        counters[scope_id] = counters.get(scope_id, 0) + 1
        connection.execute(
            sa.text(f"UPDATE {table} SET version = :version WHERE {primary_key} = :row_id"),
            {"version": counters[scope_id], "row_id": row["row_id"]},
        )


def upgrade() -> None:
    _renumber("teaching_briefs", "brief_id", fallback_scope="session_id")
    _renumber("courseware_plans", "plan_id")
    _renumber("artifact_versions", "artifact_version_id")

    with op.batch_alter_table("teaching_briefs") as batch_op:
        batch_op.create_unique_constraint(
            "uq_teaching_briefs_project_version", ["project_id", "version"]
        )
        batch_op.create_unique_constraint(
            "uq_teaching_briefs_session_version", ["session_id", "version"]
        )

    op.drop_index("ix_courseware_plans_project_version", table_name="courseware_plans")
    with op.batch_alter_table("courseware_plans") as batch_op:
        batch_op.create_unique_constraint(
            "uq_courseware_plans_project_version", ["project_id", "version"]
        )

    op.drop_index("ix_artifact_versions_project_version", table_name="artifact_versions")
    with op.batch_alter_table("artifact_versions") as batch_op:
        batch_op.create_unique_constraint(
            "uq_artifact_versions_project_version", ["project_id", "version"]
        )


def downgrade() -> None:
    with op.batch_alter_table("artifact_versions") as batch_op:
        batch_op.drop_constraint("uq_artifact_versions_project_version", type_="unique")
    op.create_index(
        "ix_artifact_versions_project_version",
        "artifact_versions",
        ["project_id", "version"],
    )

    with op.batch_alter_table("courseware_plans") as batch_op:
        batch_op.drop_constraint("uq_courseware_plans_project_version", type_="unique")
    op.create_index(
        "ix_courseware_plans_project_version",
        "courseware_plans",
        ["project_id", "version"],
    )

    with op.batch_alter_table("teaching_briefs") as batch_op:
        batch_op.drop_constraint("uq_teaching_briefs_session_version", type_="unique")
        batch_op.drop_constraint("uq_teaching_briefs_project_version", type_="unique")
