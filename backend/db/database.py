"""数据库连接与会话管理"""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from config import settings

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI 依赖 — 每次请求注入一个数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
