"""Registration, login and current-user APIs."""

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from backend.config import settings
from backend.core.errors import ApiError
from backend.core.security import create_access_token, get_current_user, hash_password, verify_password
from backend.db.database import get_db
from backend.models.user import User
from backend.schemas import LoginRequest, RegisterRequest, TokenResponse, UserInfo

router = APIRouter()


def _user_info(user: User) -> UserInfo:
    return UserInfo.model_validate(user)


def _token_response(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user),
        expires_in=settings.access_token_expire_minutes * 60,
        user=_user_info(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(req: RegisterRequest, db: DBSession = Depends(get_db)):
    email = str(req.email).strip().lower()
    if db.query(User).filter(User.email == email).first() is not None:
        raise ApiError(
            "该邮箱已注册",
            code="EMAIL_ALREADY_EXISTS",
            status_code=409,
            suggested_action="请直接登录或使用其他邮箱注册",
        )

    display_name = req.display_name.strip() or email.split("@", 1)[0]
    user = User(
        email=email,
        display_name=display_name,
        password_hash=hash_password(req.password),
        role="teacher",
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError("该邮箱已注册", code="EMAIL_ALREADY_EXISTS", status_code=409) from exc
    db.refresh(user)
    return _token_response(user)


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: DBSession = Depends(get_db)):
    email = str(req.email).strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user is None or not user.is_active or not verify_password(req.password, user.password_hash):
        raise ApiError(
            "邮箱或密码错误",
            code="INVALID_CREDENTIALS",
            status_code=401,
            recoverable=False,
            suggested_action="请检查登录信息后重试",
        )
    return _token_response(user)


@router.get("/me", response_model=UserInfo)
def me(user: User = Depends(get_current_user)):
    return _user_info(user)
