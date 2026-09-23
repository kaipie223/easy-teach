from backend.db.database import Base
from .session import gen_id
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String
from datetime import datetime, timezone


class Task(Base):
    """课件生成任务表 — 异步任务状态追踪"""

    __tablename__ = "tasks"

    task_id = Column(String, primary_key=True, default=lambda: gen_id("task"))
    user_id = Column(String, nullable=True, index=True)
    project_id = Column(String, nullable=True, index=True)
    plan_id = Column(String, nullable=True, index=True)
    artifact_version_id = Column(
        String,
        ForeignKey("artifact_versions.artifact_version_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    session_id = Column(String, nullable=False)
    task_type = Column(String, nullable=False, default="generation")  # generation / export
    status = Column(String, default="pending")        # pending / processing / completed / failed
    progress = Column(Integer, default=0)             # 0-100，由阶段派生
    stage = Column(String(48), nullable=True)         # services/progress.py 的阶段键
    stage_label = Column(String(128), nullable=True)  # 比步骤更细的当前动作
    stage_started_at = Column(DateTime(timezone=True), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=2)
    celery_task_id = Column(String(255), nullable=True)
    idempotency_key = Column(String(128), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String(128), nullable=True)
    outputs = Column(JSON, nullable=True)             # 生成完成后填充，文件信息列表
    error = Column(String, nullable=True)             # 失败时的错误描述
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_tasks_celery_task_id", "celery_task_id"),
        Index("ix_tasks_idempotency_key", "user_id", "idempotency_key", unique=True),
        Index("ix_tasks_status_heartbeat", "status", "heartbeat_at"),
    )
