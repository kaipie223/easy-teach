import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Text, JSON

from backend.db.database import Base


def gen_id(prefix: str) -> str:
    """生成带前缀的唯一 ID，格式：{prefix}_{8位十六进制}"""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class Session(Base):
    """备课会话表（聚合根）"""

    __tablename__ = "sessions"

    session_id = Column(String, primary_key=True, default=lambda: gen_id("s"))
    user_id = Column(String, nullable=True, index=True)
    project_id = Column(String, nullable=True, index=True)
    teacher_name = Column(String, default="")
    subject = Column(String, default="")
    status = Column(String, default="active")  # active / completed
    intent_state = Column(String, nullable=True, default="init")
    intent_data = Column(JSON, nullable=True)
    current_brief_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ChatMessage(Base):
    """对话消息表"""

    __tablename__ = "chat_messages"

    id = Column(String, primary_key=True, default=lambda: gen_id("msg"))
    user_id = Column(String, nullable=True, index=True)
    project_id = Column(String, nullable=True, index=True)
    session_id = Column(String, nullable=False)
    role = Column(String, nullable=False)            # user / assistant
    content = Column(Text, nullable=False)
    msg_type = Column(String, default="text")        # text / question / confirm
    event_data = Column(JSON, nullable=True)          # structured SSE payload
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
