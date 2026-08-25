"""Knowledge-base document persistence model."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint

from backend.db.database import Base
from .session import gen_id


class KnowledgeDocument(Base):
    """A managed document in the shared knowledge-base collection."""

    __tablename__ = "knowledge_documents"

    document_id = Column(String(40), primary_key=True, default=lambda: gen_id("kb"))
    owner_id = Column(String(40), ForeignKey("users.user_id", ondelete="SET NULL"), index=True)
    collection_id = Column(String(128), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    source_path = Column(String(1024), nullable=False)
    file_type = Column(String(32), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    checksum_sha256 = Column(String(64), nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=False, index=True)
    index_status = Column(String(32), nullable=False, default="pending", index=True)
    index_namespace = Column(String(128))
    metadata_json = Column(JSON, nullable=False, default=dict)
    error_code = Column(String(128))
    error_message = Column(Text)
    indexed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    deleted_at = Column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "collection_id", "checksum_sha256", "version", name="uq_knowledge_document_version"
        ),
    )
