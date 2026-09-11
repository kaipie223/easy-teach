"""Make knowledge documents private to one teacher."""

from alembic import op
import sqlalchemy as sa


revision = "0009_private_teacher_knowledge"
down_revision = "0008_courseware_ai_metadata"
branch_labels = None
depends_on = None


def _constraint_name(
    constraints: list[dict],
    columns: list[str],
    fallback: str,
) -> str:
    """Return the reflected name for a constraint on exactly these columns."""
    expected = set(columns)
    for constraint in constraints:
        constrained_columns = constraint.get("constrained_columns") or constraint.get(
            "column_names"
        )
        if set(constrained_columns or []) == expected and constraint.get("name"):
            return str(constraint["name"])
    return fallback


def upgrade() -> None:
    # Ownerless rows belonged to the former shared knowledge base. Remove them
    # instead of silently exposing them to an arbitrary teacher.
    op.execute(
        "DELETE FROM evidence_chunks WHERE knowledge_document_id IN "
        "(SELECT document_id FROM knowledge_documents WHERE owner_id IS NULL)"
    )
    op.execute("DELETE FROM knowledge_documents WHERE owner_id IS NULL")

    naming_convention = {
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    }
    inspector = sa.inspect(op.get_bind())
    unique_name = _constraint_name(
        inspector.get_unique_constraints("knowledge_documents"),
        ["collection_id", "checksum_sha256", "version"],
        "uq_knowledge_document_version",
    )
    owner_fk_name = _constraint_name(
        inspector.get_foreign_keys("knowledge_documents"),
        ["owner_id"],
        "fk_knowledge_documents_owner_id_users",
    )
    with op.batch_alter_table(
        "knowledge_documents",
        naming_convention=naming_convention,
    ) as batch_op:
        batch_op.drop_constraint(unique_name, type_="unique")
        batch_op.drop_constraint(owner_fk_name, type_="foreignkey")
        batch_op.alter_column(
            "owner_id",
            existing_type=sa.String(length=40),
            nullable=False,
        )
        batch_op.create_foreign_key(
            "fk_knowledge_documents_owner_id_users",
            "users",
            ["owner_id"],
            ["user_id"],
            ondelete="CASCADE",
        )
        batch_op.create_unique_constraint(
            "uq_knowledge_owner_collection_checksum_version",
            ["owner_id", "collection_id", "checksum_sha256", "version"],
        )


def downgrade() -> None:
    naming_convention = {
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    }
    inspector = sa.inspect(op.get_bind())
    private_unique_name = _constraint_name(
        inspector.get_unique_constraints("knowledge_documents"),
        ["owner_id", "collection_id", "checksum_sha256", "version"],
        "uq_knowledge_owner_collection_checksum_version",
    )
    owner_fk_name = _constraint_name(
        inspector.get_foreign_keys("knowledge_documents"),
        ["owner_id"],
        "fk_knowledge_documents_owner_id_users",
    )
    with op.batch_alter_table(
        "knowledge_documents",
        naming_convention=naming_convention,
    ) as batch_op:
        batch_op.drop_constraint(private_unique_name, type_="unique")
        batch_op.drop_constraint(owner_fk_name, type_="foreignkey")
        batch_op.alter_column(
            "owner_id",
            existing_type=sa.String(length=40),
            nullable=True,
        )
        batch_op.create_foreign_key(
            "fk_knowledge_documents_owner_id_users",
            "users",
            ["owner_id"],
            ["user_id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            "uq_knowledge_document_version",
            ["collection_id", "checksum_sha256", "version"],
        )
