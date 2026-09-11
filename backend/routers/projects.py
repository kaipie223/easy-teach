"""Project CRUD and soft-delete recovery."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session as DBSession

from backend.core.errors import ApiError
from backend.core.ownership import get_project_for_user
from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.models.project import Project
from backend.models.session import Session
from backend.models.user import User
from backend.schemas import ProjectCreate, ProjectInfo, ProjectUpdate

router = APIRouter()


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
