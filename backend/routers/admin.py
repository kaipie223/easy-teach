"""Administrator-only APIs."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DBSession

from backend.core.security import require_admin
from backend.db.database import get_db
from backend.models.user import User
from backend.schemas import UserInfo

router = APIRouter()


@router.get("/users", response_model=list[UserInfo])
def list_users(
    db: DBSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    return db.query(User).order_by(User.created_at.desc()).all()
