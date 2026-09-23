"""M5 immutable artifact versions, revision patches and export records."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
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


class ArtifactVersion(Base):
    """Immutable CoursewarePlan snapshot used by editing and export workflows."""

    __tablename__ = "artifact_versions"

    artifact_version_id = Column(String(40), primary_key=True, default=lambda: gen_id("av"))
    user_id = Column(
        String(40), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id = Column(
        String(40), ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_plan_id = Column(
        String(40), ForeignKey("courseware_plans.plan_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    base_version_id = Column(
        String(40), ForeignKey("artifact_versions.artifact_version_id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    version = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="ready", index=True)
    summary = Column(Text, nullable=False, default="")
    snapshot_json = Column(JSON, nullable=False, default=dict)
    generation_mode = Column(String(16), nullable=False, default="manual", index=True)
    model_name = Column(String(128), nullable=True)
    prompt_version = Column(String(64), nullable=True)
    usage_json = Column(JSON, nullable=False, default=dict)
    quality_status = Column(String(32), nullable=False, default="pending")
    quality_report = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_artifact_versions_project_version"),
    )


class RevisionPatch(Base):
    """Validated, auditable edit instructions against one immutable version."""

    __tablename__ = "revision_patches"

    patch_id = Column(String(40), primary_key=True, default=lambda: gen_id("patch"))
    user_id = Column(
        String(40), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id = Column(
        String(40), ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False, index=True
    )
    base_version_id = Column(
        String(40), ForeignKey("artifact_versions.artifact_version_id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    created_version_id = Column(
        String(40), ForeignKey("artifact_versions.artifact_version_id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    instruction = Column(Text, nullable=False, default="")
    scope = Column(String(32), nullable=False, default="slide")
    target_ids = Column(JSON, nullable=False, default=list)
    operations_json = Column(JSON, nullable=False, default=list)
    cascade_check = Column(JSON, nullable=False, default=list)
    requires_confirmation = Column(Boolean, nullable=False, default=False)
    status = Column(String(32), nullable=False, default="preview", index=True)
    summary = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    applied_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_revision_patches_project_status", "project_id", "status"),
    )


class ExportRecord(Base):
    """One export attempt bound to one immutable artifact version."""

    __tablename__ = "exports"

    export_id = Column(String(40), primary_key=True, default=lambda: gen_id("export"))
    user_id = Column(
        String(40), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id = Column(
        String(40), ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_version_id = Column(
        String(40), ForeignKey("artifact_versions.artifact_version_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    format = Column(String(16), nullable=False)
    status = Column(String(32), nullable=False, default="pending", index=True)
    stage = Column(String(48), nullable=True)          # services/progress.py 的阶段键
    stage_started_at = Column(DateTime(timezone=True), nullable=True)
    file_id = Column(String(40), ForeignKey("files.file_id", ondelete="SET NULL"), nullable=True)
    path = Column(String(1024), nullable=True)
    file_name = Column(String(255), nullable=True)
    checksum_sha256 = Column(String(64), nullable=True)
    size_bytes = Column(Integer, nullable=True)
    celery_task_id = Column(String(255), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=2)
    started_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_exports_version_format", "artifact_version_id", "format"),
        Index("ix_exports_celery_task_id", "celery_task_id"),
        Index("ix_exports_status_updated", "status", "updated_at"),
    )
