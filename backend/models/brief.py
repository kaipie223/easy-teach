"""Versioned TeachingBrief persistence model."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, JSON, String

from backend.db.database import Base
from .session import gen_id


class TeachingBrief(Base):
    __tablename__ = "teaching_briefs"

    brief_id = Column(String, primary_key=True, default=lambda: gen_id("brief"))
    user_id = Column(String, nullable=True, index=True)
    project_id = Column(String, nullable=True, index=True)
    session_id = Column(String, nullable=True, index=True)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String, nullable=False, default="draft")  # draft / confirmed
    content_json = Column(JSON, nullable=False, default=dict)
    source_refs = Column(JSON, nullable=False, default=dict)
    confidence = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
