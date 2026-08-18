"""M5 — 课件生成 API"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session as DBSession

from backend.core.errors import ApiError
from backend.core.ownership import get_session_for_user, get_task_for_user
from backend.core.security import get_optional_current_user
from backend.db.database import get_db
from backend.models.user import User
from backend.schemas import FeedbackRequest, FeedbackResponse, GenerateRequest, TaskInfo, TaskStatus
from backend.services.brief import get_latest_brief
from backend.services.orchestrator import get_orchestrator

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/generate", response_model=TaskInfo, status_code=202)
async def start_generation(
    req: GenerateRequest,
    background_tasks: BackgroundTasks,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(get_optional_current_user),
):
    if not req.session_id.strip():
        raise ApiError("session_id 不能为空", code="missing_session_id", status_code=422)

    session = get_session_for_user(db, req.session_id, user)
    if session.project_id:
        brief = get_latest_brief(db, project_id=session.project_id)
        if brief is None or brief.status != "confirmed":
            raise ApiError(
                "请先确认 TeachingBrief 再生成课件",
                code="BRIEF_NOT_CONFIRMED",
                status_code=409,
                suggested_action="补充需求确认单并点击确认后再生成",
            )
    orchestrator = get_orchestrator()
    task_info = orchestrator.create_generation_task(session, db)

    # 后台任务使用独立数据库会话，不传递请求级 db 实例
    background_tasks.add_task(orchestrator.run_generation, task_info.task_id)

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

    return TaskInfo(
        task_id=t.task_id,
        session_id=t.session_id,
        project_id=t.project_id,
        status=TaskStatus(t.status),
        progress=t.progress,
        outputs=t.outputs,
        error=t.error,
    )


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
