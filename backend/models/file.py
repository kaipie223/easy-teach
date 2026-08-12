from db.database import Base
from .session import gen_id
from sqlalchemy import Column, String, Float, DateTime
from datetime import datetime, timezone


class FileRecord(Base):
    """文件记录表 — 教师上传的参考资料元信息"""

    __tablename__ = "files"

    file_id = Column(String, primary_key=True, default=lambda: gen_id("f"))
    session_id = Column(String, nullable=False)
    original_name = Column(String, nullable=False)
    file_type = Column(String, nullable=False)        # pdf / word / ppt / image / video
    stored_path = Column(String, nullable=False)
    size_kb = Column(Float, default=0)
    ref_description = Column(String, nullable=True)   # 教师备注，传给 AI 指导生成风格
    upload_time = Column(DateTime, default=lambda: datetime.now(timezone.utc))
