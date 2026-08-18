"""Teaching project persistence model."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String

from backend.db.database import Base
from .session import gen_id


class Project(Base):
    __tablename__ = "projects"

    project_id = Column(String, primary_key=True, default=lambda: gen_id("p"))
    owner_id = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    scenario = Column(String, nullable=False, default="")
    status = Column(String, nullable=False, default="active")
    current_version_id = Column(String, nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
