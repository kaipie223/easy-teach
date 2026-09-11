"""Track AI and manual artifact version provenance.

Revision ID: 0011_artifact_ai_metadata
Revises: 0010_project_version_constraints
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_artifact_ai_metadata"
down_revision = "0010_project_version_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "artifact_versions",
        sa.Column(
            "generation_mode",
            sa.String(length=16),
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column("artifact_versions", sa.Column("model_name", sa.String(length=128)))
    op.add_column("artifact_versions", sa.Column("prompt_version", sa.String(length=64)))
    op.add_column(
        "artifact_versions",
        sa.Column("usage_json", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_artifact_versions_generation_mode",
        "artifact_versions",
        ["generation_mode"],
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_versions_generation_mode", table_name="artifact_versions")
    op.drop_column("artifact_versions", "usage_json")
    op.drop_column("artifact_versions", "prompt_version")
    op.drop_column("artifact_versions", "model_name")
    op.drop_column("artifact_versions", "generation_mode")
