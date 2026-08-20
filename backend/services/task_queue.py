"""Durable task lifecycle helpers shared by API routes and Celery tasks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from backend.config import settings
from backend.core.errors import ApiError
from backend.db.database import SessionLocal
from backend.models.task import Task
from backend.models.versioning import ExportRecord


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def task_info_values(task: Task) -> dict:
    return {
        "task_id": task.task_id,
        "session_id": task.session_id,
        "project_id": task.project_id,
        "plan_id": task.plan_id,
        "artifact_version_id": task.artifact_version_id,
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress or 0,
        "retry_count": task.retry_count or 0,
        "max_retries": task.max_retries or settings.task_max_retries,
        "error_code": task.error_code,
        "started_at": task.started_at,
        "updated_at": task.updated_at,
        "completed_at": task.completed_at,
        "outputs": task.outputs,
        "error": task.error,
    }


def claim_task_attempt(db: DBSession, task_id: str) -> bool:
    """Claim a generation task, leaving fresh work owned by its current worker."""
    task = db.query(Task).filter(Task.task_id == task_id).with_for_update().first()
    if task is None or task.status == "completed":
        return False
    now = utcnow()
    if task.status == "processing":
        heartbeat = task.heartbeat_at or task.started_at
        if heartbeat is not None and now - heartbeat < timedelta(seconds=settings.task_stale_after_seconds):
            return False
        if (task.retry_count or 0) >= (task.max_retries or settings.task_max_retries):
            task.status = "failed"
            task.error_code = "TASK_STALE_MAX_RETRIES"
            task.error = "任务工作进程失联，已超过最大恢复次数"
            task.completed_at = now
            task.updated_at = now
            db.commit()
            return False
        task.retry_count = (task.retry_count or 0) + 1

    task.status = "processing"
    task.started_at = task.started_at or now
    task.heartbeat_at = now
    task.updated_at = now
    task.error = None
    task.error_code = None
    db.commit()
    return True


def touch_task(db: DBSession, task: Task, progress: int | None = None) -> None:
    if progress is not None:
        task.progress = max(0, min(100, progress))
    now = utcnow()
    task.heartbeat_at = now
    task.updated_at = now
    db.commit()


def prepare_task_retry(task_id: str, error: Exception | str) -> bool:
    """Move a failed task back to pending when another retry is available."""
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.task_id == task_id).with_for_update().first()
        if task is None or task.status == "completed":
            return False
        now = utcnow()
        message = str(error)
        if (task.retry_count or 0) < (task.max_retries or settings.task_max_retries):
            task.retry_count = (task.retry_count or 0) + 1
            task.status = "pending"
            task.error_code = "TASK_RETRYING"
            task.error = message
            task.completed_at = None
            task.heartbeat_at = None
            task.updated_at = now
            db.commit()
            return True
        task.status = "failed"
        task.error_code = "TASK_FAILED"
        task.error = message
        task.completed_at = now
        task.updated_at = now
        db.commit()
        return False
    finally:
        db.close()


def claim_export_attempt(db: DBSession, export_id: str) -> bool:
    record = db.query(ExportRecord).filter(ExportRecord.export_id == export_id).with_for_update().first()
    if record is None or record.status == "completed":
        return False
    now = utcnow()
    if record.status == "processing":
        heartbeat = record.updated_at or record.started_at
        if heartbeat is not None and now - heartbeat < timedelta(seconds=settings.task_stale_after_seconds):
            return False
        if (record.retry_count or 0) >= (record.max_retries or settings.task_max_retries):
            record.status = "failed"
            record.error = "导出工作进程失联，已超过最大恢复次数"
            record.updated_at = now
            db.commit()
            return False
        record.retry_count = (record.retry_count or 0) + 1

    record.status = "processing"
    record.started_at = record.started_at or now
    record.updated_at = now
    record.error = None
    db.commit()
    return True


def touch_export(db: DBSession, record: ExportRecord) -> None:
    record.updated_at = utcnow()
    db.commit()


def prepare_export_retry(export_id: str, error: Exception | str) -> bool:
    db = SessionLocal()
    try:
        record = db.query(ExportRecord).filter(ExportRecord.export_id == export_id).with_for_update().first()
        if record is None or record.status == "completed":
            return False
        now = utcnow()
        message = str(error)
        if (record.retry_count or 0) < (record.max_retries or settings.task_max_retries):
            record.retry_count = (record.retry_count or 0) + 1
            record.status = "pending"
            record.error = message
            record.updated_at = now
            db.commit()
            return True
        record.status = "failed"
        record.error = message
        record.updated_at = now
        db.commit()
        return False
    finally:
        db.close()


def _dispatch(task_name: str, entity_id: str):
    if not settings.task_queue_enabled:
        raise ApiError(
            "任务队列未启用",
            code="TASK_QUEUE_DISABLED",
            status_code=503,
            recoverable=False,
            suggested_action="启用 Redis 和 Celery worker 后重试",
        )

    from backend.celery_app import celery_app

    try:
        if settings.task_queue_eager:
            from backend.tasks import generate_courseware_task, render_export_task

            task = generate_courseware_task if task_name == "easy_teach.generate" else render_export_task
            return task.apply_async(args=[entity_id], task_id=entity_id)
        return celery_app.send_task(
            task_name,
            args=[entity_id],
            task_id=entity_id,
            queue=settings.task_queue_name,
        )
    except Exception as exc:
        raise ApiError(
            "任务队列不可用，任务未入队",
            code="TASK_QUEUE_UNAVAILABLE",
            status_code=503,
            details=str(exc),
            suggested_action="检查 Redis 连接和 Celery worker 状态后重试",
        ) from exc


def enqueue_generation(task_id: str, *, force: bool = False, db: DBSession | None = None) -> str:
    owns_db = db is None
    db = db or SessionLocal()
    try:
        task = db.query(Task).filter(Task.task_id == task_id).first()
        if task is None:
            raise ApiError("生成任务不存在", code="TASK_NOT_FOUND", status_code=404)
        if task.status == "completed":
            return task.celery_task_id or task.task_id
        if task.status == "failed" and (task.retry_count or 0) >= (task.max_retries or settings.task_max_retries):
            return task.celery_task_id or task.task_id
        if task.celery_task_id and not force:
            return task.celery_task_id
        result = _dispatch("easy_teach.generate", task_id)
        task.celery_task_id = result.id
        task.updated_at = utcnow()
        db.commit()
        return result.id
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(
            "生成任务入队失败",
            code="TASK_QUEUE_UNAVAILABLE",
            status_code=503,
            details=str(exc),
        ) from exc
    finally:
        if owns_db:
            db.close()


def enqueue_export(export_id: str, *, force: bool = False, db: DBSession | None = None) -> str:
    owns_db = db is None
    db = db or SessionLocal()
    try:
        record = db.query(ExportRecord).filter(ExportRecord.export_id == export_id).first()
        if record is None:
            raise ApiError("导出记录不存在", code="EXPORT_NOT_FOUND", status_code=404)
        if record.status == "completed":
            return record.celery_task_id or record.export_id
        if record.status == "failed" and (record.retry_count or 0) >= (record.max_retries or settings.task_max_retries):
            return record.celery_task_id or record.export_id
        if record.celery_task_id and not force:
            return record.celery_task_id
        result = _dispatch("easy_teach.export", export_id)
        record.celery_task_id = result.id
        record.updated_at = utcnow()
        db.commit()
        return result.id
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(
            "导出任务入队失败",
            code="TASK_QUEUE_UNAVAILABLE",
            status_code=503,
            details=str(exc),
        ) from exc
    finally:
        if owns_db:
            db.close()


def recover_stale_jobs() -> dict[str, int]:
    """Recover jobs whose worker heartbeat has exceeded the configured lease."""
    cutoff = utcnow() - timedelta(seconds=settings.task_stale_after_seconds)
    generation_ids: list[str] = []
    export_ids: list[str] = []
    recovered = 0
    failed = 0
    db = SessionLocal()
    try:
        tasks = (
            db.query(Task)
            .filter(
                Task.status == "processing",
                or_(Task.heartbeat_at < cutoff, Task.heartbeat_at.is_(None)),
            )
            .with_for_update()
            .all()
        )
        for task in tasks:
            if (task.retry_count or 0) < (task.max_retries or settings.task_max_retries):
                task.retry_count = (task.retry_count or 0) + 1
                task.status = "pending"
                task.error_code = "TASK_STALE_RECOVERED"
                task.error = "任务工作进程失联，已自动恢复"
                task.heartbeat_at = None
                task.celery_task_id = None
                generation_ids.append(task.task_id)
                recovered += 1
            else:
                task.status = "failed"
                task.error_code = "TASK_STALE_MAX_RETRIES"
                task.error = "任务工作进程失联，已超过最大恢复次数"
                task.completed_at = utcnow()
                failed += 1

        exports = (
            db.query(ExportRecord)
            .filter(
                ExportRecord.status == "processing",
                or_(ExportRecord.updated_at < cutoff, ExportRecord.updated_at.is_(None)),
            )
            .with_for_update()
            .all()
        )
        for record in exports:
            if (record.retry_count or 0) < (record.max_retries or settings.task_max_retries):
                record.retry_count = (record.retry_count or 0) + 1
                record.status = "pending"
                record.error = "导出工作进程失联，已自动恢复"
                record.celery_task_id = None
                export_ids.append(record.export_id)
                recovered += 1
            else:
                record.status = "failed"
                record.error = "导出工作进程失联，已超过最大恢复次数"
                failed += 1
        db.commit()
    finally:
        db.close()

    for task_id in generation_ids:
        enqueue_generation(task_id, force=True)
    for export_id in export_ids:
        enqueue_export(export_id, force=True)
    return {"recovered": recovered, "failed": failed}
