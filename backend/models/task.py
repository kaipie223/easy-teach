from backend.db.database import Base
from .session import gen_id
from sqlalchemy import Column, String, Integer, DateTime, JSON
from datetime import datetime, timezone


class Task(Base):
    """课件生成任务表 — 异步任务状态追踪"""

    __tablename__ = "tasks"

    task_id = Column(String, primary_key=True, default=lambda: gen_id("task"))
    user_id = Column(String, nullable=True, index=True)
    project_id = Column(String, nullable=True, index=True)
    session_id = Column(String, nullable=False)
    status = Column(String, default="pending")        # pending / processing / completed / failed
    progress = Column(Integer, default=0)             # 0-100
    outputs = Column(JSON, nullable=True)             # 生成完成后填充，文件信息列表
    error = Column(String, nullable=True)             # 失败时的错误描述
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
