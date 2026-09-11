"""Celery entry points for generation and export jobs."""

from __future__ import annotations

from celery.exceptions import SoftTimeLimitExceeded

from backend.celery_app import celery_app
from backend.config import settings
from backend.services.exports import run_export
from backend.services.materials import prepare_material_retry, run_material_analysis
from backend.services.orchestrator import get_orchestrator
from backend.services.task_queue import (
    prepare_export_retry,
    prepare_task_retry,
    recover_stale_jobs as recover_stale_jobs_sync,
)


def _retry_delay(retry_count: int) -> int:
    return min(300, settings.task_retry_backoff_seconds * max(1, 2**retry_count))


@celery_app.task(
    bind=True,
    name="easy_teach.generate",
    max_retries=10,
    acks_late=True,
    reject_on_worker_lost=True,
)
def generate_courseware_task(self, task_id: str):
    try:
        get_orchestrator().run_generation(task_id, raise_errors=True)
    except SoftTimeLimitExceeded as exc:
        if prepare_task_retry(task_id, "生成任务超过软超时限制"):
            raise self.retry(exc=exc, countdown=_retry_delay(self.request.retries))
        raise
    except Exception as exc:
        if prepare_task_retry(task_id, exc):
            raise self.retry(exc=exc, countdown=_retry_delay(self.request.retries))
        raise
    return task_id


@celery_app.task(
    bind=True,
    name="easy_teach.export",
    max_retries=10,
    acks_late=True,
    reject_on_worker_lost=True,
)
def render_export_task(self, export_id: str):
    try:
        run_export(export_id, raise_errors=True)
    except SoftTimeLimitExceeded as exc:
        if prepare_export_retry(export_id, "导出任务超过软超时限制"):
            raise self.retry(exc=exc, countdown=_retry_delay(self.request.retries))
        raise
    except Exception as exc:
        if prepare_export_retry(export_id, exc):
            raise self.retry(exc=exc, countdown=_retry_delay(self.request.retries))
        raise
    return export_id


@celery_app.task(
    bind=True,
    name="easy_teach.parse_material",
    max_retries=10,
    acks_late=True,
    reject_on_worker_lost=True,
)
def parse_material_task(self, analysis_id: str):
    try:
        run_material_analysis(analysis_id, raise_errors=True)
    except SoftTimeLimitExceeded as exc:
        if self.request.retries < settings.task_max_retries:
            prepare_material_retry(analysis_id, "视频解析任务超过软超时限制")
            raise self.retry(exc=exc, countdown=_retry_delay(self.request.retries))
        raise
    except Exception as exc:
        if self.request.retries < settings.task_max_retries:
            prepare_material_retry(analysis_id, exc)
            raise self.retry(exc=exc, countdown=_retry_delay(self.request.retries))
        raise
    return analysis_id


@celery_app.task(name="easy_teach.recover_stale_jobs")
def recover_stale_jobs():
    return recover_stale_jobs_sync()
