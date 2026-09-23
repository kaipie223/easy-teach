"""Database migration revision checks for startup logging and health reporting.

Running against a database that is behind the migrations fails with opaque 500s
on whichever endpoints touch the missing columns. Comparing the recorded
revision with the code's head revision turns that into one actionable message.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from backend.config import PROJECT_ROOT
from backend.db.database import engine

logger = logging.getLogger(__name__)

UPGRADE_COMMAND = "uv run alembic upgrade head"


@lru_cache(maxsize=1)
def head_revision() -> str | None:
    """Return the head revision declared by the alembic scripts."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        return ScriptDirectory.from_config(config).get_current_head()
    except Exception:
        logger.warning("无法解析 alembic head 版本", exc_info=True)
        return None


def database_revision() -> str | None:
    """Return the revision recorded in the database, or None when unversioned."""
    with engine.connect() as connection:
        if not inspect(connection).has_table("alembic_version"):
            return None
        row = connection.execute(text("SELECT version_num FROM alembic_version")).first()
    if row is None or row[0] is None:
        return None
    return str(row[0])


def schema_check() -> dict[str, str]:
    """Report whether the database schema matches the code's migrations."""
    head = head_revision()
    try:
        current = database_revision()
    except SQLAlchemyError as exc:
        logger.warning("无法读取数据库迁移版本：%s", exc)
        return {"status": "unknown", "message": str(exc)}

    if current is None:
        return {
            "status": "unversioned",
            "head": head or "",
            "suggested_action": UPGRADE_COMMAND,
        }
    if head is not None and current != head:
        return {
            "status": "outdated",
            "current": current,
            "head": head,
            "suggested_action": UPGRADE_COMMAND,
        }
    return {"status": "ok", "current": current, "head": head or current}


def log_schema_status() -> dict[str, str]:
    """Log the migration status once at startup so a stale schema is obvious."""
    result = schema_check()
    status = result["status"]
    if status == "ok":
        logger.info("数据库迁移版本已对齐：%s", result.get("current"))
    elif status == "outdated":
        logger.error(
            "数据库迁移版本落后：当前 %s，期望 %s；缺失的列会让相关接口返回 500，请执行 `%s`",
            result.get("current"),
            result.get("head"),
            UPGRADE_COMMAND,
        )
    elif status == "unversioned":
        logger.error("数据库未记录任何迁移版本，请执行 `%s`", UPGRADE_COMMAND)
    else:
        logger.warning("无法确认数据库迁移版本：%s", result.get("message"))
    return result
