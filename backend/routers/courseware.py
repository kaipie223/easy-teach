"""M4 courseware-plan and project generation APIs."""

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session as DBSession

from backend.core.errors import ApiError
from backend.core.ownership import get_project_for_user
from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.models.project import Project
from backend.models.session import Session
from backend.models.user import User
from backend.schemas import (
    CoursewareGenerateRequest,
    CoursewarePlanBuildRequest,
    CoursewarePlanInfo,
    TaskInfo,
)
from backend.services.brief import get_latest_brief
from backend.services.courseware import (
    build_courseware_plan,
    get_plan_for_project,
    get_latest_plan,
    to_info,
)
from backend.services.orchestrator import get_orchestrator
from backend.services.rag import search as rag_search
from backend.services.task_queue import enqueue_generation
from backend.services.versions import ensure_initial_version

router = APIRouter()


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


@router.post("/{project_id}/plan", response_model=CoursewarePlanInfo, status_code=201)
async def create_plan(
    project_id: str,
    request: CoursewarePlanBuildRequest | None = Body(default=None),
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    brief = get_latest_brief(db, project_id=project.project_id)
    if brief is None or brief.status != "confirmed":
        # Keep the error consistent before any potentially expensive RAG call.
        build_courseware_plan(db, project)

    force_rebuild = request.force_rebuild if request else False
    current = get_latest_plan(db, project.project_id)
    if current is not None and current.brief_id == brief.brief_id and not force_rebuild:
        return to_info(current)

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
    rag_docs = await rag_search(query, top_k=5)
    plan = build_courseware_plan(
        db,
        project,
        rag_docs=rag_docs,
        force_rebuild=force_rebuild,
    )
    db.commit()
    db.refresh(plan)
    return to_info(plan)


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
        idempotency_key=request.idempotency_key if request else None,
    )
    enqueue_generation(task_info.task_id, db=db)
    return task_info
