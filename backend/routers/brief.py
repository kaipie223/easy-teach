"""TeachingBrief draft, edit and confirmation APIs."""

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session as DBSession

from backend.core.ownership import get_project_for_user
from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.models.project import Project
from backend.models.session import Session
from backend.models.user import User
from backend.schemas import (
    TeachingBriefConfirmRequest,
    TeachingBriefInfo,
    TeachingBriefUpdate,
)
from backend.services.brief import confirm_draft, get_latest_brief, to_info, update_draft

router = APIRouter()


def _latest_session(db: DBSession, project: Project) -> Session | None:
    return (
        db.query(Session)
        .filter(Session.project_id == project.project_id)
        .order_by(Session.created_at.desc())
        .first()
    )


@router.get("/{project_id}/brief", response_model=TeachingBriefInfo)
def get_brief(
    project_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    brief = get_latest_brief(db, project_id=project.project_id)
    if brief is None:
        from backend.core.errors import ApiError

        raise ApiError("需求确认单尚未生成", code="BRIEF_NOT_FOUND", status_code=404)
    return to_info(brief)


@router.patch("/{project_id}/brief", response_model=TeachingBriefInfo)
def patch_brief(
    project_id: str,
    changes: TeachingBriefUpdate,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    session = _latest_session(db, project)
    brief = update_draft(
        db,
        user_id=project.owner_id,
        project_id=project.project_id,
        session_id=session.session_id if session else None,
        changes=changes,
    )
    db.commit()
    db.refresh(brief)
    return to_info(brief)


@router.post("/{project_id}/brief/confirm", response_model=TeachingBriefInfo)
def confirm_brief(
    project_id: str,
    request: TeachingBriefConfirmRequest | None = Body(default=None),
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    brief = confirm_draft(
        db,
        project=project,
        expected_version=request.expected_version if request else None,
    )
    db.commit()
    db.refresh(brief)
    return to_info(brief)
