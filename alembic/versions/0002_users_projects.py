"""Add users, projects and ownership columns."""

from alembic import op
import sqlalchemy as sa

revision = "0002_users_projects"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "projects",
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("scenario", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("current_version_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"], unique=False)

    for table_name in ("sessions", "chat_messages", "files", "tasks"):
        op.add_column(table_name, sa.Column("user_id", sa.String(), nullable=True))
        op.add_column(table_name, sa.Column("project_id", sa.String(), nullable=True))
        op.create_index(f"ix_{table_name}_user_id", table_name, ["user_id"], unique=False)
        op.create_index(f"ix_{table_name}_project_id", table_name, ["project_id"], unique=False)


def downgrade() -> None:
    for table_name in ("tasks", "files", "chat_messages", "sessions"):
        op.drop_index(f"ix_{table_name}_project_id", table_name=table_name)
        op.drop_index(f"ix_{table_name}_user_id", table_name=table_name)
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_column("project_id")
            batch_op.drop_column("user_id")

    op.drop_index("ix_projects_owner_id", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
