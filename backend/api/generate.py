"""M5 — 课件生成 API"""

from fastapi import APIRouter

from models.schemas import GenerateRequest, GenerateTask, FeedbackRequest

router = APIRouter()


@router.post("/start", response_model=GenerateTask)
async def start_generation(req: GenerateRequest):
    """
    启动课件生成任务（异步），返回 task_id 用于轮询。
    由 M5 模块（赵钰洁 + 陈澜 + 姜文杰）实现。
    """
    # TODO: 创建后台任务 → 调用 PPT/Word/动画生成器
    return GenerateTask(
        task_id="placeholder",
        status="pending",
        created_at="2026-07-21T00:00:00Z",
    )


@router.get("/status/{task_id}", response_model=GenerateTask)
async def get_status(task_id: str):
    """轮询课件生成任务状态"""
    # TODO: 查询数据库
    return GenerateTask(
        task_id=task_id,
        status="processing",
        created_at="2026-07-21T00:00:00Z",
    )


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    """
    M6 — 提交修改意见，触发重新生成。
    """
    # TODO: 解析反馈 → 重新调用 M5
    return {"task_id": req.task_id, "status": "feedback_received"}
