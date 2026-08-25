"""Add material analysis, binding, evidence and knowledge-base tables."""

from alembic import op
import sqlalchemy as sa


revision = "0004_materials_and_evidence"
down_revision = "0003_teaching_briefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("document_id", sa.String(length=40), nullable=False),
        sa.Column("owner_id", sa.String(length=40), nullable=True),
        sa.Column("collection_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source_path", sa.String(length=1024), nullable=False),
        sa.Column("file_type", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("index_status", sa.String(length=32), nullable=False),
        sa.Column("index_namespace", sa.String(length=128), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.user_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("document_id"),
        sa.UniqueConstraint(
            "collection_id", "checksum_sha256", "version", name="uq_knowledge_document_version"
        ),
    )
    op.create_index("ix_knowledge_documents_owner_id", "knowledge_documents", ["owner_id"])
    op.create_index("ix_knowledge_documents_collection_id", "knowledge_documents", ["collection_id"])
    op.create_index("ix_knowledge_documents_checksum_sha256", "knowledge_documents", ["checksum_sha256"])
    op.create_index("ix_knowledge_documents_enabled", "knowledge_documents", ["enabled"])
    op.create_index("ix_knowledge_documents_index_status", "knowledge_documents", ["index_status"])

    op.create_table(
        "materials",
        sa.Column("material_id", sa.String(length=40), nullable=False),
        sa.Column("owner_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=True),
        sa.Column("session_id", sa.String(length=40), nullable=True),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=32), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("stored_path", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("ref_description", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("size_bytes >= 0", name="ck_materials_size_nonnegative"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("material_id"),
    )
    op.create_index("ix_materials_owner_id", "materials", ["owner_id"])
    op.create_index("ix_materials_project_id", "materials", ["project_id"])
    op.create_index("ix_materials_session_id", "materials", ["session_id"])
    op.create_index("ix_materials_checksum_sha256", "materials", ["checksum_sha256"])
    op.create_index("ix_materials_status", "materials", ["status"])
    op.create_index("ix_materials_project_status", "materials", ["project_id", "status"])

    op.create_table(
        "material_analyses",
        sa.Column("analysis_id", sa.String(length=40), nullable=False),
        sa.Column("material_id", sa.String(length=40), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("parser_name", sa.String(length=128), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("slide_count", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["material_id"], ["materials.material_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("analysis_id"),
        sa.UniqueConstraint("material_id", "run_number", name="uq_material_analysis_run"),
    )
    op.create_index("ix_material_analyses_material_id", "material_analyses", ["material_id"])
    op.create_index("ix_material_analyses_status", "material_analyses", ["status"])

    op.create_table(
        "material_bindings",
        sa.Column("binding_id", sa.String(length=40), nullable=False),
        sa.Column("material_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("created_by", sa.String(length=40), nullable=True),
        sa.Column("usage_type", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("teacher_instruction", sa.Text(), nullable=True),
        sa.Column("suggested_by_ai", sa.Boolean(), nullable=False),
        sa.Column("confirmed_by_teacher", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.user_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["material_id"], ["materials.material_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("binding_id"),
    )
    op.create_index("ix_material_bindings_material_id", "material_bindings", ["material_id"])
    op.create_index("ix_material_bindings_project_id", "material_bindings", ["project_id"])
    op.create_index("ix_material_bindings_created_by", "material_bindings", ["created_by"])
    op.create_index("ix_material_bindings_is_active", "material_bindings", ["is_active"])
    op.create_index("ix_material_bindings_project_active", "material_bindings", ["project_id", "is_active"])

    op.create_table(
        "evidence_chunks",
        sa.Column("evidence_id", sa.String(length=40), nullable=False),
        sa.Column("material_id", sa.String(length=40), nullable=True),
        sa.Column("knowledge_document_id", sa.String(length=40), nullable=True),
        sa.Column("analysis_id", sa.String(length=40), nullable=True),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("locator_json", sa.JSON(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("usage_tags", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("is_valid", sa.Boolean(), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(material_id IS NOT NULL AND knowledge_document_id IS NULL) "
            "OR (material_id IS NULL AND knowledge_document_id IS NOT NULL)",
            name="ck_evidence_one_source",
        ),
        sa.CheckConstraint("chunk_index >= 0", name="ck_evidence_chunk_nonnegative"),
        sa.ForeignKeyConstraint(["analysis_id"], ["material_analyses.analysis_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["knowledge_document_id"], ["knowledge_documents.document_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["material_id"], ["materials.material_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("evidence_id"),
    )
    op.create_index("ix_evidence_chunks_material_id", "evidence_chunks", ["material_id"])
    op.create_index("ix_evidence_chunks_knowledge_document_id", "evidence_chunks", ["knowledge_document_id"])
    op.create_index("ix_evidence_chunks_analysis_id", "evidence_chunks", ["analysis_id"])
    op.create_index("ix_evidence_chunks_content_hash", "evidence_chunks", ["content_hash"])
    op.create_index("ix_evidence_chunks_is_valid", "evidence_chunks", ["is_valid"])
    op.create_index("ix_evidence_source_valid", "evidence_chunks", ["source_type", "is_valid"])


def downgrade() -> None:
    op.drop_index("ix_evidence_source_valid", table_name="evidence_chunks")
    op.drop_index("ix_evidence_chunks_is_valid", table_name="evidence_chunks")
    op.drop_index("ix_evidence_chunks_content_hash", table_name="evidence_chunks")
    op.drop_index("ix_evidence_chunks_analysis_id", table_name="evidence_chunks")
    op.drop_index("ix_evidence_chunks_knowledge_document_id", table_name="evidence_chunks")
    op.drop_index("ix_evidence_chunks_material_id", table_name="evidence_chunks")
    op.drop_table("evidence_chunks")

    op.drop_index("ix_material_bindings_project_active", table_name="material_bindings")
    op.drop_index("ix_material_bindings_is_active", table_name="material_bindings")
    op.drop_index("ix_material_bindings_created_by", table_name="material_bindings")
    op.drop_index("ix_material_bindings_project_id", table_name="material_bindings")
    op.drop_index("ix_material_bindings_material_id", table_name="material_bindings")
    op.drop_table("material_bindings")

    op.drop_index("ix_material_analyses_status", table_name="material_analyses")
    op.drop_index("ix_material_analyses_material_id", table_name="material_analyses")
    op.drop_table("material_analyses")

    op.drop_index("ix_materials_project_status", table_name="materials")
    op.drop_index("ix_materials_status", table_name="materials")
    op.drop_index("ix_materials_checksum_sha256", table_name="materials")
    op.drop_index("ix_materials_session_id", table_name="materials")
    op.drop_index("ix_materials_project_id", table_name="materials")
    op.drop_index("ix_materials_owner_id", table_name="materials")
    op.drop_table("materials")

    op.drop_index("ix_knowledge_documents_index_status", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_enabled", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_checksum_sha256", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_collection_id", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_owner_id", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
