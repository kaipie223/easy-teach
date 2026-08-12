"""会话管理 API"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from core.errors import ApiError
from db.database import get_db
from models.session import Session
from schemas import SessionCreate, SessionInfo

router = APIRouter()


@router.post("", response_model=SessionInfo, status_code=201)
def create_session(req: SessionCreate, db: DBSession = Depends(get_db)):
    sid = f"s_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    session = Session(
        session_id=sid,
        teacher_name=req.teacher_name,
        subject=req.subject,
        status="active",
        created_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return SessionInfo(
        session_id=session.session_id,
        teacher_name=session.teacher_name,
        subject=session.subject,
        status=session.status,
        created_at=session.created_at,
    )


@router.get("", response_model=list[SessionInfo])
def list_sessions(db: DBSession = Depends(get_db)):
    sessions = db.query(Session).order_by(Session.created_at.desc()).all()
    return [
        SessionInfo(
            session_id=s.session_id,
            teacher_name=s.teacher_name,
            subject=s.subject,
            status=s.status,
            created_at=s.created_at,
        )
        for s in sessions
    ]


@router.get("/{session_id}", response_model=SessionInfo)
def get_session(session_id: str, db: DBSession = Depends(get_db)):
    s = db.query(Session).filter(Session.session_id == session_id).first()
    if not s:
        raise ApiError("会话不存在", code="SESSION_NOT_FOUND", status_code=404)
    return SessionInfo(
        session_id=s.session_id,
        teacher_name=s.teacher_name,
        subject=s.subject,
        status=s.status,
        created_at=s.created_at,
    )
