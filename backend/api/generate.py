"""M5 - Courseware generation API."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter

from core.errors import ApiError
from models.schemas import FeedbackRequest, FeedbackResponse, GenerateRequest, GenerateTask, TaskStatus

router = APIRouter()


@router.post("/start", response_model=GenerateTask)
async def start_generation(req: GenerateRequest):
    if not req.session_id.strip():
        raise ApiError("session_id is required.", code="missing_session_id", status_code=422)

    return GenerateTask(
        task_id=str(uuid4()),
        status=TaskStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )


@router.get("/status/{task_id}", response_model=GenerateTask)
async def get_status(task_id: str):
    if not task_id.strip():
        raise ApiError("task_id is required.", code="missing_task_id", status_code=422)

    return GenerateTask(
        task_id=task_id,
        status=TaskStatus.PROCESSING,
        created_at=datetime.now(timezone.utc),
    )


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(req: FeedbackRequest):
    if not req.feedback.strip():
        raise ApiError("feedback cannot be empty.", code="empty_feedback", status_code=422)

    return FeedbackResponse(task_id=req.task_id, status="feedback_received")
