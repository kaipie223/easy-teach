"""M5 — 课件生成 API"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DBSession

from backend.core.errors import ApiError
from backend.core.ownership import get_project_for_user, get_session_for_user, get_task_for_user
from backend.core.security import get_current_user
from backend.config import settings
from backend.db.database import get_db
from backend.models.courseware import CoursewarePlan
from backend.models.project import Project
from backend.models.user import User
from backend.services.courseware import get_latest_plan
from backend.schemas import FeedbackRequest, FeedbackResponse, GenerateRequest, TaskInfo
from backend.services.brief import get_latest_brief
from backend.services.orchestrator import get_orchestrator
from backend.services.task_queue import enqueue_generation, task_info_values
from backend.services.versions import (
    apply_operations,
    create_patch,
    ensure_initial_version,
    get_latest_version,
    get_version,
    interpret_instruction,
    snapshot_spec,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/generate", response_model=TaskInfo, status_code=202)
async def start_generation(
    req: GenerateRequest,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not req.session_id.strip():
        raise ApiError("session_id 不能为空", code="missing_session_id", status_code=422)

    session = get_session_for_user(db, req.session_id, user)
    artifact_version_id = None
    if session.project_id:
        brief = get_latest_brief(db, project_id=session.project_id)
        if brief is None or brief.status != "confirmed":
            raise ApiError(
                "请先确认 TeachingBrief 再生成课件",
                code="BRIEF_NOT_CONFIRMED",
                status_code=409,
                suggested_action="补充需求确认单并点击确认后再生成",
            )
        if req.plan_id:
            plan = (
                db.query(CoursewarePlan)
                .filter(
                    CoursewarePlan.plan_id == req.plan_id,
                    CoursewarePlan.project_id == session.project_id,
                )
                .first()
            )
            if plan is None:
                raise ApiError("教学蓝图不存在", code="PLAN_NOT_FOUND", status_code=404)
        else:
            plan = get_latest_plan(db, session.project_id)
        if plan is not None:
            project = session.project_id and db.query(Project).filter(Project.project_id == session.project_id).first()
            if project is not None:
                artifact_version = ensure_initial_version(
                    db,
                    project,
                    plan,
                    user_id=session.user_id or project.owner_id,
                )
                artifact_version_id = artifact_version.artifact_version_id
                db.commit()
    orchestrator = get_orchestrator()
    task_info = orchestrator.create_generation_task(
        session,
        db,
        plan_id=req.plan_id or (plan.plan_id if session.project_id and plan is not None else None),
        artifact_version_id=artifact_version_id,
        idempotency_key=req.idempotency_key,
    )

    # Celery worker 使用独立数据库会话，不传递请求级 db 实例。
    enqueue_generation(task_info.task_id, db=db)
    if settings.task_queue_eager:
        db.expire_all()
        refreshed = get_task_for_user(db, task_info.task_id, user)
        return TaskInfo(**task_info_values(refreshed))
    return task_info


@router.get("/tasks/{task_id}/status", response_model=TaskInfo)
def get_task_status(
    task_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not task_id.strip():
        raise ApiError("task_id 不能为空", code="missing_task_id", status_code=422)

    t = get_task_for_user(db, task_id, user)

    return TaskInfo(**task_info_values(t))


@router.post("/generate/feedback", response_model=FeedbackResponse, status_code=201)
@router.post("/feedback", response_model=FeedbackResponse, status_code=201, include_in_schema=False)
def submit_feedback(
    req: FeedbackRequest,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not req.feedback.strip():
        raise ApiError("反馈内容不能为空", code="empty_feedback", status_code=422)
    task = get_task_for_user(db, req.task_id, user)
    if not task.project_id:
        raise ApiError(
            "该生成任务未绑定项目，无法创建成果修订",
            code="FEEDBACK_PROJECT_REQUIRED",
            status_code=409,
        )
    project = get_project_for_user(db, task.project_id, user)
    base = (
        get_version(db, project.project_id, task.artifact_version_id)
        if task.artifact_version_id
        else get_latest_version(db, project.project_id)
    )
    if base is None:
        raise ApiError(
            "该任务没有可修改的成果版本",
            code="FEEDBACK_VERSION_REQUIRED",
            status_code=409,
            suggested_action="先完成教学成果生成，再提交修改意见",
        )

    scope, target_ids, operations, cascade_check, requires_confirmation, summary = (
        interpret_instruction(snapshot_spec(base), req.feedback)
    )
    apply_operations(snapshot_spec(base), operations)
    patch = create_patch(
        db,
        user_id=user.user_id,
        project_id=project.project_id,
        base_version_id=base.artifact_version_id,
        instruction=req.feedback,
        scope=scope,
        target_ids=target_ids,
        operations=operations,
        cascade_check=cascade_check,
        requires_confirmation=requires_confirmation,
        summary=summary,
    )
    db.commit()
    db.refresh(patch)
    return FeedbackResponse(
        task_id=task.task_id,
        project_id=project.project_id,
        patch_id=patch.patch_id,
        status="revision_preview_created",
        requires_confirmation=patch.requires_confirmation,
    )
