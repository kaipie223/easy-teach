"""M5 — 课件生成 API"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DBSession

from backend.core.errors import ApiError
from backend.core.ownership import get_session_for_user, get_task_for_user
from backend.core.security import get_optional_current_user
from backend.db.database import get_db
from backend.models.courseware import CoursewarePlan
from backend.models.project import Project
from backend.models.user import User
from backend.services.courseware import get_latest_plan
from backend.schemas import FeedbackRequest, FeedbackResponse, GenerateRequest, TaskInfo
from backend.services.brief import get_latest_brief
from backend.services.orchestrator import get_orchestrator
from backend.services.task_queue import enqueue_generation, task_info_values
from backend.services.versions import ensure_initial_version

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/generate", response_model=TaskInfo, status_code=202)
async def start_generation(
    req: GenerateRequest,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(get_optional_current_user),
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

    return task_info


@router.get("/tasks/{task_id}/status", response_model=TaskInfo)
def get_task_status(
    task_id: str,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(get_optional_current_user),
):
    if not task_id.strip():
        raise ApiError("task_id 不能为空", code="missing_task_id", status_code=422)

    t = get_task_for_user(db, task_id, user)

    return TaskInfo(**task_info_values(t))


@router.post("/feedback", response_model=FeedbackResponse)
def submit_feedback(
    req: FeedbackRequest,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(get_optional_current_user),
):
    if not req.feedback.strip():
        raise ApiError("反馈内容不能为空", code="empty_feedback", status_code=422)
    get_task_for_user(db, req.task_id, user)
    return FeedbackResponse(task_id=req.task_id, status="feedback_received")
