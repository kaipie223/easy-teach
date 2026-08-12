"""M5 — 课件生成 API"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session as DBSession

from core.errors import ApiError
from db.database import get_db
from models.task import Task
from schemas import FeedbackRequest, FeedbackResponse, GenerateRequest, TaskInfo, TaskStatus
from services.orchestrator import get_orchestrator

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/generate", response_model=TaskInfo, status_code=202)
async def start_generation(
    req: GenerateRequest,
    background_tasks: BackgroundTasks,
    db: DBSession = Depends(get_db),
):
    if not req.session_id.strip():
        raise ApiError("session_id 不能为空", code="missing_session_id", status_code=422)

    orchestrator = get_orchestrator()
    task_info = orchestrator.create_generation_task(req.session_id, db)

    # 后台任务使用独立数据库会话，不传递请求级 db 实例
    background_tasks.add_task(orchestrator.run_generation, task_info.task_id)

    return task_info


@router.get("/tasks/{task_id}/status", response_model=TaskInfo)
def get_task_status(task_id: str, db: DBSession = Depends(get_db)):
    if not task_id.strip():
        raise ApiError("task_id 不能为空", code="missing_task_id", status_code=422)

    t = db.query(Task).filter(Task.task_id == task_id).first()
    if not t:
        raise ApiError("任务不存在", code="TASK_NOT_FOUND", status_code=404)

    return TaskInfo(
        task_id=t.task_id,
        session_id=t.session_id,
        status=TaskStatus(t.status),
        progress=t.progress,
        outputs=t.outputs,
        error=t.error,
    )


@router.post("/feedback", response_model=FeedbackResponse)
def submit_feedback(req: FeedbackRequest):
    if not req.feedback.strip():
        raise ApiError("反馈内容不能为空", code="empty_feedback", status_code=422)
    return FeedbackResponse(task_id=req.task_id, status="feedback_received")
