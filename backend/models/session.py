import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Text

from db.database import Base


def gen_id(prefix: str) -> str:
    """生成带前缀的唯一 ID，格式：{prefix}_{8位十六进制}"""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class Session(Base):
    """备课会话表（聚合根）"""

    __tablename__ = "sessions"

    session_id = Column(String, primary_key=True, default=lambda: gen_id("s"))
    teacher_name = Column(String, default="")
    subject = Column(String, default="")
    status = Column(String, default="active")  # active / completed
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ChatMessage(Base):
    """对话消息表"""

    __tablename__ = "chat_messages"

    id = Column(String, primary_key=True, default=lambda: gen_id("msg"))
    session_id = Column(String, nullable=False)
    role = Column(String, nullable=False)            # user / assistant
    content = Column(Text, nullable=False)
    msg_type = Column(String, default="text")        # text / question / confirm
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
