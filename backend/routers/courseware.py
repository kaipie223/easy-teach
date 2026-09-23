"""M4 courseware-plan and project generation APIs."""

import logging
from typing import Any, AsyncIterator, Callable

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DBSession

from backend.core.errors import ApiError
from backend.config import settings
from backend.core.ownership import get_project_for_user, get_task_for_user
from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.models.project import Project
from backend.models.session import Session
from backend.models.user import User
from backend.schemas import (
    CoursewareGenerateRequest,
    CoursewarePlanBuildRequest,
    CoursewarePlanInfo,
    CoursewarePlanRevisionRequest,
    TaskInfo,
)
from backend.services.ai_stream import aiter_threaded_producer
from backend.services.brief import get_confirmed_brief
from backend.services.courseware import (
    build_courseware_plan,
    get_plan_for_project,
    get_latest_plan,
    revise_courseware_plan,
    to_info,
)
from backend.services.orchestrator import get_orchestrator
from backend.services.limits import consume_model_quota
from backend.services.rag import search_sync
from backend.services.sse import (
    SSE_HEADERS,
    SSE_MEDIA_TYPE,
    encode_sse,
    error_frame,
    wants_event_stream,
)
from backend.services.progress import PLAN_BRANCHES, PLAN_STAGES, frame
from backend.services.task_queue import enqueue_generation, task_info_values
from backend.services.versions import ensure_initial_version

router = APIRouter()
logger = logging.getLogger(__name__)




def _latest_session(db: DBSession, project: Project) -> Session | None:
    return (
        db.query(Session)
        .filter(Session.project_id == project.project_id)
        .order_by(Session.created_at.desc())
        .first()
    )


@router.get("/{project_id}/plan", response_model=CoursewarePlanInfo)
def get_plan(
    project_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    plan = get_latest_plan(db, project.project_id)
    if plan is None:
        raise ApiError("教学蓝图尚未生成", code="PLAN_NOT_FOUND", status_code=404)
    return to_info(plan)


@router.post("/{project_id}/plan/revisions", response_model=CoursewarePlanInfo, status_code=201)
def create_plan_revision(
    project_id: str,
    request: CoursewarePlanRevisionRequest,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    base = get_plan_for_project(db, project.project_id, request.base_plan_id)
    if base is None:
        raise ApiError("教学蓝图不存在", code="PLAN_NOT_FOUND", status_code=404)
    plan = revise_courseware_plan(
        db,
        project,
        base,
        request.content,
        summary=request.summary,
    )
    ensure_initial_version(db, project, plan, user_id=user.user_id)
    db.commit()
    db.refresh(plan)
    return to_info(plan)


def _build_plan_payload(
    db: DBSession,
    project_id: str,
    user: User,
    request: CoursewarePlanBuildRequest | None,
    *,
    on_stage: Callable[[str], None] | None = None,
) -> CoursewarePlanInfo:
    """Shared blueprint build used by both the JSON and the streaming response."""
    project = get_project_for_user(db, project_id, user)
    brief = get_confirmed_brief(db, project_id=project.project_id)
    if brief is None:
        # Delegate the error to the builder so the BRIEF_NOT_CONFIRMED contract
        # stays in one place, before any potentially expensive RAG call.
        build_courseware_plan(db, project)

    force_rebuild = request.force_rebuild if request else False
    generation_mode = request.generation_mode if request else "ai"
    current = get_latest_plan(db, project.project_id)
    if (
        current is not None
        and current.brief_id == brief.brief_id
        and current.generation_mode == generation_mode
        and not force_rebuild
    ):
        return to_info(current)

    if generation_mode == "ai":
        consume_model_quota(user.user_id)

    content = brief.content_json if brief else {}
    query = " ".join(
        [
            str(content.get("teaching_goal", "")),
            *[
                str(item.get("title", ""))
                for item in (content.get("knowledge_points", []) or [])
                if isinstance(item, dict)
            ],
        ]
    ).strip()
    rag_docs = search_sync(query, top_k=5, owner_id=project.owner_id)
    plan = build_courseware_plan(
        db,
        project,
        rag_docs=rag_docs,
        force_rebuild=force_rebuild,
        generation_mode=generation_mode,
        allow_template_fallback=request.allow_template_fallback if request else False,
        on_stage=on_stage,
    )
    ensure_initial_version(db, project, plan, user_id=user.user_id)
    db.commit()
    db.refresh(plan)
    return to_info(plan)


async def _plan_event_stream(
    db: DBSession,
    project_id: str,
    user: User,
    request: CoursewarePlanBuildRequest | None,
) -> AsyncIterator[str]:
    """Emit progress frames while the blueprint builds, then the finished plan."""

    def run(emit: Callable[[str, Any], None]) -> None:
        def on_stage(stage: str) -> None:
            emit("progress", frame(PLAN_STAGES, stage, branches=PLAN_BRANCHES))

        payload = _build_plan_payload(db, project_id, user, request, on_stage=on_stage)
        emit("result", payload.model_dump(mode="json"))

    try:
        async for kind, payload in aiter_threaded_producer(run):
            yield encode_sse(kind, payload)
    except ApiError as exc:
        logger.warning("Streamed blueprint build failed: %s", exc.code)
        yield error_frame(
            exc.message,
            code=exc.code,
            recoverable=exc.recoverable,
            suggested_action=exc.suggested_action,
        )
    except Exception:
        logger.exception("Streamed blueprint build crashed")
        yield error_frame(
            "生成教学蓝图时出错，请重试",
            code="PLAN_STREAM_FAILED",
            suggested_action="请稍后重试；若持续失败，请联系管理员",
        )


@router.post("/{project_id}/plan", response_model=CoursewarePlanInfo, status_code=201)
def create_plan(
    http_request: Request,
    project_id: str,
    request: CoursewarePlanBuildRequest | None = Body(default=None),
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Build the teaching blueprint for a project.

    A client sending `Accept: text/event-stream` receives stage progress frames
    and then the finished blueprint, which keeps a multi-minute generation
    observable. Every other client keeps the plain JSON response unchanged.
    """
    if not wants_event_stream(http_request.headers.get("accept")):
        return _build_plan_payload(db, project_id, user, request)

    return StreamingResponse(
        _plan_event_stream(db, project_id, user, request),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )


@router.post("/{project_id}/generate", response_model=TaskInfo, status_code=202)
def generate_project(
    project_id: str,
    request: CoursewareGenerateRequest | None = Body(default=None),
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    plan = get_plan_for_project(db, project.project_id, request.plan_id if request else None)
    if plan is None:
        raise ApiError(
            "教学蓝图尚未生成",
            code="PLAN_NOT_FOUND",
            status_code=409,
            suggested_action="先生成教学蓝图再生成成果",
        )
    session = _latest_session(db, project)
    if session is None:
        raise ApiError(
            "项目尚未创建会话",
            code="SESSION_NOT_FOUND",
            status_code=409,
            suggested_action="先进入需求共创并创建会话",
        )

    artifact_version = ensure_initial_version(
        db,
        project,
        plan,
        user_id=user.user_id,
    )
    db.commit()
    orchestrator = get_orchestrator()
    task_info = orchestrator.create_generation_task(
        session,
        db,
        plan_id=plan.plan_id,
        artifact_version_id=artifact_version.artifact_version_id,
        # 幂等键由服务端按"这次生成对应的蓝图快照"派生。客户端只能给出 "latest"，
        # 换蓝图后仍会命中同一个旧任务，于是"重新生成"看起来毫无反应。同一个
        # 版本重复点击仍复用同一个任务，因此不会堆积重复任务。
        idempotency_key=(
            f"project-generation:{project.project_id}:"
            f"{artifact_version.artifact_version_id}"
        ),
    )
    enqueue_generation(task_info.task_id, db=db)
    if settings.task_queue_eager:
        db.expire_all()
        refreshed = get_task_for_user(db, task_info.task_id, user)
        return TaskInfo(**task_info_values(refreshed))
    return task_info
