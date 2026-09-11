"""Password hashing and JWT authentication dependencies."""

from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session as DBSession

from backend.config import settings
from backend.core.errors import ApiError
from backend.db.database import get_db
from backend.models.user import User

password_hasher = PasswordHasher()
bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": user.user_id,
        "role": user.role,
        "iat": now,
        "exp": expires_at,
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _auth_error(message: str = "需要登录后才能访问此资源") -> ApiError:
    return ApiError(
        message,
        code="AUTH_REQUIRED",
        status_code=401,
        recoverable=False,
        suggested_action="请先登录并在请求头中携带 Bearer Token",
    )


def _decode_user(credentials: HTTPAuthorizationCredentials, db: DBSession) -> User:
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise _auth_error("登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise _auth_error("登录凭证无效，请重新登录") from exc

    user_id = payload.get("sub")
    if not user_id or payload.get("type") != "access":
        raise _auth_error("登录凭证无效，请重新登录")

    user = db.query(User).filter(User.user_id == str(user_id)).first()
    if user is None or not user.is_active:
        raise _auth_error("用户不存在或已停用")
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: DBSession = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _auth_error()
    return _decode_user(credentials, db)


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise ApiError(
            "只有管理员可以访问此资源",
            code="ADMIN_REQUIRED",
            status_code=403,
            recoverable=False,
            suggested_action="请联系管理员申请权限",
        )
    return user


def require_teacher(user: User = Depends(get_current_user)) -> User:
    if user.role != "teacher":
        raise ApiError(
            "只有教师可以访问此资源",
            code="TEACHER_REQUIRED",
            status_code=403,
            recoverable=False,
            suggested_action="请使用教师账号登录",
        )
    return user
