"""Celery application definition used by the API and worker processes."""

from celery import Celery

from backend.config import settings


celery_app = Celery(
    "easy_teach",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["backend.tasks"],
)

celery_app.conf.update(
    task_default_queue=settings.task_queue_name,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=settings.task_time_limit_seconds,
    task_soft_time_limit=settings.task_soft_time_limit_seconds,
    broker_connection_retry_on_startup=True,
    task_always_eager=settings.task_queue_eager,
    task_eager_propagates=True,
    beat_schedule={
        "recover-stale-easy-teach-jobs": {
            "task": "easy_teach.recover_stale_jobs",
            "schedule": 60.0,
        }
    },
)
