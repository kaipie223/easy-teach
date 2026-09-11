"""M2 — 文件上传 API"""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session as DBSession

from backend.config import settings
from backend.core.errors import ApiError
from backend.core.ownership import get_file_for_user, get_session_for_user
from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.models.file import FileRecord
from backend.models.user import User
from backend.schemas import FileInfo
from backend.services.limits import remaining_storage_bytes
from backend.services.uploads import UploadSizeExceeded, stream_upload_to_path

router = APIRouter()

ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".pptx",
}


def _detect_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in {".pdf"}:
        return "pdf"
    elif ext in {".docx"}:
        return "word"
    elif ext in {".pptx"}:
        return "ppt"
    return "unknown"


@router.post("/upload", response_model=FileInfo, status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    ref_description: str = Form(""),
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not file.filename:
        raise ApiError("文件名为空", code="invalid_file", status_code=400)

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ApiError(
            f"不支持的文件格式: {ext}",
            code="unsupported_format",
            status_code=400,
            details={"allowed": sorted(ALLOWED_EXTENSIONS)},
        )

    file_type = _detect_file_type(file.filename)
    session = get_session_for_user(db, session_id, user)

    # 保存文件到磁盘 — 强制提取纯净文件名，杜绝路径遍历攻击
    safe_filename = Path(file.filename).name
    file_id = f"f_{uuid.uuid4().hex[:24]}"
    stored_path = settings.upload_dir / file_id / safe_filename
    stored_path.parent.mkdir(parents=True, exist_ok=True)

    max_size = settings.max_upload_size_mb * 1024 * 1024
    remaining_bytes = remaining_storage_bytes(db, user.user_id)
    if remaining_bytes <= 0:
        raise ApiError(
            "个人存储空间不足",
            code="STORAGE_QUOTA_EXCEEDED",
            status_code=413,
            suggested_action="请删除不再需要的资料后重试",
        )
    try:
        stored = await stream_upload_to_path(
            file,
            stored_path,
            max_bytes=min(max_size, remaining_bytes),
        )
    except UploadSizeExceeded as exc:
        quota_limited = remaining_bytes < max_size
        raise ApiError(
            "个人存储空间不足" if quota_limited else f"文件超过 {settings.max_upload_size_mb} MB 限制",
            code="STORAGE_QUOTA_EXCEEDED" if quota_limited else "file_too_large",
            status_code=413,
            details={"max_bytes": exc.max_bytes, "actual_bytes": exc.actual_bytes},
            suggested_action="请删除资料或压缩文件后重试",
        ) from exc
    size_kb = round(stored.size_bytes / 1024, 2)

    # 记录到数据库 — original_name 保留用户原始文件名用于展示
    record = FileRecord(
        file_id=file_id,
        user_id=session.user_id,
        project_id=session.project_id,
        session_id=session.session_id,
        original_name=safe_filename,
        file_type=file_type,
        stored_path=str(stored_path),
        size_kb=size_kb,
        ref_description=ref_description or None,
        upload_time=datetime.now(timezone.utc),
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return FileInfo(
        file_id=record.file_id,
        original_name=record.original_name,
        file_type=file_type,
        size_kb=record.size_kb,
        upload_time=record.upload_time,
        ref_description=record.ref_description,
    )


@router.get("/files/{file_id}", response_model=FileInfo)
def get_file_info(
    file_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    f = get_file_for_user(db, file_id, user)
    return FileInfo(
        file_id=f.file_id,
        original_name=f.original_name,
        file_type=f.file_type,
        size_kb=f.size_kb,
        upload_time=f.upload_time,
        ref_description=f.ref_description,
    )
