"""Project CRUD and soft-delete recovery."""

import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DBSession

from backend.core.errors import ApiError
from backend.core.ownership import get_project_for_user
from backend.core.security import get_current_user
from backend.db.database import SessionLocal, get_db
from backend.models.project import Project
from backend.models.session import Session
from backend.models.user import User
from backend.schemas import ProjectCreate, ProjectInfo, ProjectUpdate
from backend.services.project_progress import build_project_progress
from backend.services.sse import (
    SSE_HEADERS,
    SSE_MEDIA_TYPE,
    encode_sse,
    error_frame,
    wants_event_stream,
)
from backend.services.watch import watch_snapshot

router = APIRouter()
logger = logging.getLogger(__name__)


def _to_info(project: Project, db: DBSession) -> ProjectInfo:
    session = (
        db.query(Session)
        .filter(Session.project_id == project.project_id)
        .order_by(Session.created_at.desc())
        .first()
    )
    return ProjectInfo(
        project_id=project.project_id,
        owner_id=project.owner_id,
        title=project.title,
        scenario=project.scenario,
        status=project.status,
        session_id=session.session_id if session else None,
        created_at=project.created_at,
        updated_at=project.updated_at,
        deleted_at=project.deleted_at,
    )


@router.get("", response_model=list[ProjectInfo])
def list_projects(
    include_deleted: bool = Query(False),
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Project).filter(Project.owner_id == user.user_id)
    if not include_deleted:
        query = query.filter(Project.deleted_at.is_(None))
    projects = query.order_by(Project.updated_at.desc(), Project.created_at.desc()).all()
    return [_to_info(project, db) for project in projects]


@router.post("", response_model=ProjectInfo, status_code=201)
def create_project(
    req: ProjectCreate,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    title = req.title.strip()
    if not title:
        raise ApiError("项目标题不能为空", code="INVALID_PROJECT_TITLE", status_code=422)
    now = datetime.now(timezone.utc)
    project = Project(
        owner_id=user.user_id,
        title=title,
        scenario=req.scenario.strip(),
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _to_info(project, db)


@router.get("/{project_id}", response_model=ProjectInfo)
def get_project(
    project_id: str,
    include_deleted: bool = Query(False),
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user, include_deleted=include_deleted)
    return _to_info(project, db)


@router.patch("/{project_id}", response_model=ProjectInfo)
def update_project(
    project_id: str,
    req: ProjectUpdate,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    if req.title is not None:
        title = req.title.strip()
        if not title:
            raise ApiError("项目标题不能为空", code="INVALID_PROJECT_TITLE", status_code=422)
        project.title = title
    if req.scenario is not None:
        project.scenario = req.scenario.strip()
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(project)
    return _to_info(project, db)


@router.delete("/{project_id}", response_model=ProjectInfo)
def delete_project(
    project_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user, include_deleted=True)
    if project.deleted_at is None:
        project.deleted_at = datetime.now(timezone.utc)
        project.status = "deleted"
        project.updated_at = project.deleted_at
        db.commit()
        db.refresh(project)
    return _to_info(project, db)


@router.post("/{project_id}/restore", response_model=ProjectInfo)
def restore_project(
    project_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user, include_deleted=True)
    if project.deleted_at is not None:
        project.deleted_at = None
        project.status = "active"
        project.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(project)
    return _to_info(project, db)


def _project_snapshot_reader(project_id: str) -> Callable[[], dict[str, Any] | None]:
    """Return a repeated-read helper that opens its own short-lived session.

    The watcher calls it from a worker thread on every tick, so it must not reuse
    the request-scoped session, whose identity map would keep returning the first
    snapshot it loaded.
    """

    def snapshot() -> dict[str, Any] | None:
        session = SessionLocal()
        try:
            return build_project_progress(session, project_id)
        finally:
            session.close()

    return snapshot


async def _project_event_stream(project_id: str) -> AsyncIterator[str]:
    try:
        # A project keeps producing work, so this stream never reaches a terminal
        # state on its own: it stays subscribed and only the time cap closes it.
        async for kind, payload in watch_snapshot(
            _project_snapshot_reader(project_id),
            is_terminal=lambda _payload: False,
        ):
            yield encode_sse(kind, payload)
    except ApiError as exc:
        logger.warning("Project progress stream failed: %s", exc.code)
        yield error_frame(
            exc.message,
            code=exc.code,
            recoverable=exc.recoverable,
            suggested_action=exc.suggested_action,
        )
    except Exception:
        logger.exception("Project progress stream crashed")
        yield error_frame(
            "获取项目进度失败，请稍后重试",
            code="PROJECT_STREAM_FAILED",
            suggested_action="刷新页面后重试",
        )


@router.get("/{project_id}/events")
def watch_project_progress(
    http_request: Request,
    project_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Stream the project's in-flight work so views can stop polling.

    A frame is emitted whenever the set of running tasks, exports or material
    parses changes. The stream stays open for the project's lifetime, so work
    started later is reported without the client resubscribing. Clients that do
    not negotiate `text/event-stream` receive the current snapshot as JSON.
    """
    project = get_project_for_user(db, project_id, user)
    if not wants_event_stream(http_request.headers.get("accept")):
        return build_project_progress(db, project.project_id)

    return StreamingResponse(
        _project_event_stream(project.project_id),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
