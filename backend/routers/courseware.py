"""M4 courseware-plan and project generation APIs."""

import asyncio
import logging
import threading
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
from backend.services.plan_jobs import acquire_plan_job, release_plan_job
from backend.services.progress import PLAN_BRANCHES, PLAN_STAGES, frame, label_of
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
    deep_thinking = request.deep_thinking if request else None
    current = get_latest_plan(db, project.project_id)
    if (
        current is not None
        and current.brief_id == brief.brief_id
        and current.generation_mode == generation_mode
        and not force_rebuild
        # 与 build_courseware_plan 保持同一条规则：勾了深度思考就必须真的重算，
        # 否则这里先把缓存返回了，开关看起来完全没作用。
        and not deep_thinking
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
        deep_thinking=deep_thinking,
        on_stage=on_stage,
    )
    ensure_initial_version(db, project, plan, user_id=user.user_id)
    db.commit()
    db.refresh(plan)
    return to_info(plan)


def _progress_emitter(job) -> Callable[..., None]:
    """把流水线的阶段回调接到作业的进度帧上（含阶段内细分文案）。"""

    def on_stage(stage: str, detail: str | None = None) -> None:
        # 未知阶段会被 percent_of 静默当成 0：进度条不是停住，而是**被打回起点**，
        # 比不动更难解释。这类失误要等流水线跑几分钟才看得见，所以在这里立刻喊一声。
        if label_of(PLAN_STAGES, stage, PLAN_BRANCHES) is None:
            logger.warning(
                "蓝图进度：阶段 %r 不在进度表里，进度会退回 0（请补进 PLAN_BRANCHES）", stage
            )
        job.emit("progress", frame(PLAN_STAGES, stage, branches=PLAN_BRANCHES, detail=detail))

    return on_stage


def _start_plan_build(
    db: DBSession,
    project_id: str,
    user: User,
    request: CoursewarePlanBuildRequest | None,
    job,
) -> None:
    """owner 在自己的线程里跑生成，结果与异常都投给作业。"""

    def run() -> None:
        try:
            payload = _build_plan_payload(
                db, project_id, user, request, on_stage=_progress_emitter(job)
            )
            job.emit("result", payload.model_dump(mode="json"))
        except BaseException as exc:  # noqa: BLE001 - 转交订阅方处理
            logger.warning("蓝图生成失败（作业 %s）：%s", project_id, exc)
            job.fail(exc)
        finally:
            job.close()
            release_plan_job(project_id, job)

    threading.Thread(target=run, name=f"plan-build-{project_id}", daemon=True).start()


def _join_running_build(project_id: str, job) -> CoursewarePlanInfo:
    """已经有同一项目的生成在跑：等它出结果，不再另起一条。"""
    payload = job.wait_for_result()
    if job.error is not None:
        raise job.error
    if payload is None:
        raise ApiError("教学蓝图生成未完成，请重试", code="PLAN_BUILD_INCOMPLETE")
    return CoursewarePlanInfo.model_validate(payload)


async def _plan_event_stream(
    db: DBSession,
    project_id: str,
    user: User,
    request: CoursewarePlanBuildRequest | None,
) -> AsyncIterator[str]:
    """Emit progress frames while the blueprint builds, then the finished plan.

    生成期间重复进入页面（刷新、返回再进）会再次 POST 到这里 —— 那不该变成
    第二条流水线，而是订阅已经在跑的那一条。
    """
    job, is_owner = acquire_plan_job(project_id)
    channel = job.subscribe()
    if is_owner:
        _start_plan_build(db, project_id, user, request, job)
    try:
        while True:
            item = await asyncio.to_thread(channel.get)
            if item is None:
                return
            kind, payload = item
            if kind == "error":
                exc = job.error
                if isinstance(exc, ApiError):
                    logger.warning("Streamed blueprint build failed: %s", exc.code)
                    yield error_frame(
                        exc.message,
                        code=exc.code,
                        recoverable=exc.recoverable,
                        suggested_action=exc.suggested_action,
                    )
                else:
                    logger.warning("Streamed blueprint build crashed: %s", exc)
                    yield error_frame(
                        "生成教学蓝图时出错，请重试",
                        code="PLAN_BUILD_FAILED",
                        recoverable=True,
                    )
                continue
            yield encode_sse(kind, payload)
    finally:
        job.unsubscribe(channel)


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
        # 非流式客户端同样不能另起一条流水线：已在跑就等它的结果
        job, is_owner = acquire_plan_job(project_id)
        if not is_owner:
            return _join_running_build(project_id, job)
        try:
            payload = _build_plan_payload(
                db, project_id, user, request, on_stage=_progress_emitter(job)
            )
            job.emit("result", payload.model_dump(mode="json"))
            return payload
        except BaseException as exc:  # noqa: BLE001 - 先广播给订阅方，再原样抛出
            job.fail(exc)
            raise
        finally:
            job.close()
            release_plan_job(project_id, job)

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
