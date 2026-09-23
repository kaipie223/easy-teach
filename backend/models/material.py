"""Persistent source-material, analysis, binding and evidence models."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)

from backend.db.database import Base
from .session import gen_id


class Material(Base):
    """A teacher-provided source file associated with a project."""

    __tablename__ = "materials"

    material_id = Column(String(40), primary_key=True, default=lambda: gen_id("mat"))
    owner_id = Column(
        String(40), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id = Column(
        String(40), ForeignKey("projects.project_id", ondelete="CASCADE"), index=True
    )
    session_id = Column(
        String(40), ForeignKey("sessions.session_id", ondelete="SET NULL"), index=True
    )
    original_name = Column(String(255), nullable=False)
    file_type = Column(String(32), nullable=False)
    mime_type = Column(String(255))
    stored_path = Column(String(1024), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    checksum_sha256 = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="uploaded", index=True)
    # 解析进度放在资料上，因为资料列表返回的就是这一行；解析本身仍由 analysis 负责。
    stage = Column(String(48))
    stage_started_at = Column(DateTime(timezone=True))
    ref_description = Column(Text)
    metadata_json = Column(JSON, nullable=False, default=dict)
    error_code = Column(String(128))
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    deleted_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_materials_size_nonnegative"),
        Index("ix_materials_project_status", "project_id", "status"),
    )


class MaterialAnalysis(Base):
    """A versioned parser run for a material."""

    __tablename__ = "material_analyses"

    analysis_id = Column(String(40), primary_key=True, default=lambda: gen_id("analysis"))
    material_id = Column(
        String(40), ForeignKey("materials.material_id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_number = Column(Integer, nullable=False, default=1)
    parser_name = Column(String(128), nullable=False)
    parser_version = Column(String(64))
    status = Column(String(32), nullable=False, default="pending", index=True)
    text_content = Column(Text)
    result_json = Column(JSON, nullable=False, default=dict)
    page_count = Column(Integer)
    slide_count = Column(Integer)
    duration_seconds = Column(Integer)
    error_code = Column(String(128))
    error_message = Column(Text)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("material_id", "run_number", name="uq_material_analysis_run"),
    )


class MaterialBinding(Base):
    """A confirmed or suggested purpose and scope for a material."""

    __tablename__ = "material_bindings"

    binding_id = Column(String(40), primary_key=True, default=lambda: gen_id("bind"))
    material_id = Column(
        String(40), ForeignKey("materials.material_id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id = Column(
        String(40), ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by = Column(String(40), ForeignKey("users.user_id", ondelete="SET NULL"), index=True)
    usage_type = Column(String(64), nullable=False)
    target_type = Column(String(64), nullable=False, default="whole_course")
    target_id = Column(String(128))
    teacher_instruction = Column(Text)
    suggested_by_ai = Column(Boolean, nullable=False, default=False)
    confirmed_by_teacher = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    invalidated_at = Column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_material_bindings_project_active", "project_id", "is_active"),
    )


class EvidenceChunk(Base):
    """An immutable, locatable source fragment usable by generation."""

    __tablename__ = "evidence_chunks"

    evidence_id = Column(String(40), primary_key=True, default=lambda: gen_id("evidence"))
    material_id = Column(
        String(40), ForeignKey("materials.material_id", ondelete="CASCADE"), index=True
    )
    knowledge_document_id = Column(
        String(40), ForeignKey("knowledge_documents.document_id", ondelete="CASCADE"), index=True
    )
    analysis_id = Column(
        String(40), ForeignKey("material_analyses.analysis_id", ondelete="SET NULL"), index=True
    )
    source_type = Column(String(64), nullable=False)
    chunk_index = Column(Integer, nullable=False, default=0)
    locator_json = Column(JSON, nullable=False, default=dict)
    text = Column(Text, nullable=False)
    metadata_json = Column(JSON, nullable=False, default=dict)
    usage_tags = Column(JSON, nullable=False, default=list)
    content_hash = Column(String(64), nullable=False, index=True)
    is_valid = Column(Boolean, nullable=False, default=True, index=True)
    invalidated_at = Column(DateTime(timezone=True))
    invalidation_reason = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        CheckConstraint(
            "(material_id IS NOT NULL AND knowledge_document_id IS NULL) "
            "OR (material_id IS NULL AND knowledge_document_id IS NOT NULL)",
            name="ck_evidence_one_source",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_evidence_chunk_nonnegative"),
        Index("ix_evidence_source_valid", "source_type", "is_valid"),
    )
