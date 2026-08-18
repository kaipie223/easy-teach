"""User and role persistence models."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, String

from backend.db.database import Base
from .session import gen_id


class User(Base):
    __tablename__ = "users"

    user_id = Column(String, primary_key=True, default=lambda: gen_id("u"))
    email = Column(String, nullable=False, unique=True, index=True)
    display_name = Column(String, nullable=False, default="")
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="teacher")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
