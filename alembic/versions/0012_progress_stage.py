"""Track which stage a long-running job is in, and when it entered it.

Revision ID: 0012_progress_stage
Revises: 0011_artifact_ai_metadata
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_progress_stage"
down_revision = "0011_artifact_ai_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Stage keys are short and stable; the human wording is derived from the key in
    # `services/progress.py`, so only the key is persisted.
    op.add_column("tasks", sa.Column("stage", sa.String(length=48)))
    # Generation's slowest step is blueprint building. Its sub-stage is finer than
    # the stepper's "build the blueprint" entry, so the wording is overridden here
    # rather than pretending there is a separate step for it.
    op.add_column("tasks", sa.Column("stage_label", sa.String(length=128)))
    op.add_column("tasks", sa.Column("stage_started_at", sa.DateTime(timezone=True)))

    op.add_column("exports", sa.Column("stage", sa.String(length=48)))
    op.add_column("exports", sa.Column("stage_started_at", sa.DateTime(timezone=True)))

    # Material progress lives on the material because that is the row the materials
    # list already returns; the analysis row keeps owning the parsing itself.
    op.add_column("materials", sa.Column("stage", sa.String(length=48)))
    op.add_column("materials", sa.Column("stage_started_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("materials", "stage_started_at")
    op.drop_column("materials", "stage")
    op.drop_column("exports", "stage_started_at")
    op.drop_column("exports", "stage")
    op.drop_column("tasks", "stage_started_at")
    op.drop_column("tasks", "stage_label")
    op.drop_column("tasks", "stage")
