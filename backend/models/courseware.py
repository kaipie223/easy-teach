"""Persisted M4 courseware blueprints."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text

from backend.db.database import Base
from .session import gen_id


class CoursewarePlan(Base):
    """An immutable version of the structured teaching blueprint."""

    __tablename__ = "courseware_plans"

    plan_id = Column(String(40), primary_key=True, default=lambda: gen_id("plan"))
    user_id = Column(
        String(40), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id = Column(
        String(40), ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False, index=True
    )
    brief_id = Column(
        String(40), ForeignKey("teaching_briefs.brief_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default="ready", index=True)
    title = Column(String(255), nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    plan_json = Column(JSON, nullable=False, default=dict)
    source_refs = Column(JSON, nullable=False, default=list)
    notes = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_courseware_plans_project_version", "project_id", "version"),
    )
