"""Cross-process request/model limits and per-user resource quotas."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from backend.config import settings
from backend.core.errors import ApiError
from backend.models.file import FileRecord
from backend.models.knowledge import KnowledgeDocument
from backend.models.material import Material
from backend.models.task import Task
from backend.models.versioning import ExportRecord


@dataclass(frozen=True)
class LimitResult:
    exceeded: bool
    retry_after: int
    limit: int


_local_lock = threading.Lock()
_local_counters: dict[str, tuple[int, float]] = {}
_redis_client = None


def _redis():
    global _redis_client
    if settings.environment.lower() not in {"production", "prod"}:
        return None
    if _redis_client is None:
        from redis import Redis

        _redis_client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
    return _redis_client


def _increment(key: str, *, window_seconds: int) -> tuple[int, int]:
    client = _redis()
    if client is not None:
        pipeline = client.pipeline()
        pipeline.incr(key)
        pipeline.ttl(key)
        count, ttl = pipeline.execute()
        if int(count) == 1 or int(ttl) < 0:
            client.expire(key, window_seconds)
            ttl = window_seconds
        return int(count), max(int(ttl), 1)

    now = time.monotonic()
    with _local_lock:
        count, expires_at = _local_counters.get(key, (0, now + window_seconds))
        if expires_at <= now:
            count, expires_at = 0, now + window_seconds
        count += 1
        _local_counters[key] = (count, expires_at)
        return count, max(int(expires_at - now), 1)


def reset_local_limits() -> None:
    """Clear development/test counters."""
    with _local_lock:
        _local_counters.clear()


def _request_identities(request: Request) -> list[str]:
    forwarded = request.headers.get("x-real-ip", "").strip()
    client_ip = forwarded or (request.client.host if request.client else "unknown")
    identities = [f"ip:{client_ip}"]
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        digest = hashlib.sha256(authorization.encode("utf-8")).hexdigest()[:24]
        identities.append(f"token:{digest}")
    return identities


def check_request_rate_limit(request: Request) -> LimitResult:
    is_auth = request.url.path.startswith("/api/v1/auth/")
    limit = (
        settings.rate_limit_auth_requests_per_minute
        if is_auth
        else settings.rate_limit_requests_per_minute
    )
    exceeded = False
    retry_after = 1
    group = "auth" if is_auth else "api"
    for identity in _request_identities(request):
        count, identity_retry_after = _increment(
            f"easy-teach:rate:{identity}:{group}",
            window_seconds=60,
        )
        exceeded = exceeded or count > limit
        retry_after = max(retry_after, identity_retry_after)
    return LimitResult(exceeded=exceeded, retry_after=retry_after, limit=limit)


def consume_model_quota(user_id: str) -> None:
    now = datetime.now(timezone.utc)
    tomorrow = datetime.combine(
        now.date() + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    ttl = max(int((tomorrow - now).total_seconds()), 1)
    key = f"easy-teach:model:{user_id}:{now.date().isoformat()}"
    count, _ = _increment(key, window_seconds=ttl)
    if count > settings.daily_model_request_limit:
        raise ApiError(
            "今日 AI 使用额度已用完",
            code="MODEL_DAILY_QUOTA_EXCEEDED",
            status_code=429,
            details={"daily_limit": settings.daily_model_request_limit},
            suggested_action="请明日再试或联系管理员调整额度",
        )


def ensure_task_capacity(db: DBSession, user_id: str, requested: int = 1) -> None:
    task_count = db.query(func.count(Task.task_id)).filter(
        Task.user_id == user_id,
        Task.status.in_(("pending", "processing")),
    ).scalar() or 0
    export_count = db.query(func.count(ExportRecord.export_id)).filter(
        ExportRecord.user_id == user_id,
        ExportRecord.status.in_(("pending", "processing")),
    ).scalar() or 0
    active = int(task_count) + int(export_count)
    if active + requested > settings.max_concurrent_tasks_per_user:
        raise ApiError(
            "当前排队或执行中的任务过多",
            code="TASK_CONCURRENCY_LIMIT_EXCEEDED",
            status_code=429,
            details={
                "active_tasks": active,
                "requested_tasks": requested,
                "limit": settings.max_concurrent_tasks_per_user,
            },
            suggested_action="请等待已有任务完成后再试",
        )


def current_storage_bytes(db: DBSession, user_id: str) -> int:
    file_bytes = int(
        (
            db.query(func.coalesce(func.sum(FileRecord.size_kb), 0))
            .filter(FileRecord.user_id == user_id)
            .scalar()
            or 0
        )
        * 1024
    )
    material_bytes = int(
        db.query(func.coalesce(func.sum(Material.size_bytes), 0))
        .filter(Material.owner_id == user_id, Material.deleted_at.is_(None))
        .scalar()
        or 0
    )
    knowledge_bytes = sum(
        int((metadata or {}).get("size_bytes", 0) or 0)
        for (metadata,) in db.query(KnowledgeDocument.metadata_json)
        .filter(
            KnowledgeDocument.owner_id == user_id,
            KnowledgeDocument.deleted_at.is_(None),
        )
        .all()
    )
    return file_bytes + material_bytes + knowledge_bytes


def remaining_storage_bytes(db: DBSession, user_id: str) -> int:
    quota = settings.storage_quota_mb_per_user * 1024 * 1024
    return max(quota - current_storage_bytes(db, user_id), 0)


def ensure_storage_capacity(db: DBSession, user_id: str, incoming_bytes: int) -> None:
    quota = settings.storage_quota_mb_per_user * 1024 * 1024
    used = current_storage_bytes(db, user_id)
    if incoming_bytes > max(quota - used, 0):
        raise ApiError(
            "个人存储空间不足",
            code="STORAGE_QUOTA_EXCEEDED",
            status_code=413,
            details={"quota_bytes": quota, "used_bytes": used},
            suggested_action="请删除不再需要的资料后重试",
        )
