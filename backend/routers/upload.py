"""M2 — 文件上传 API"""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session as DBSession

from config import settings
from core.errors import ApiError
from db.database import get_db
from models.file import FileRecord
from schemas import FileInfo

router = APIRouter()

ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".pptx",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp",
    ".mp4", ".avi", ".mov", ".mkv",
}


def _detect_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in {".pdf"}:
        return "pdf"
    elif ext in {".docx"}:
        return "word"
    elif ext in {".pptx"}:
        return "ppt"
    elif ext in {".jpg", ".jpeg", ".png", ".gif", ".bmp"}:
        return "image"
    elif ext in {".mp4", ".avi", ".mov", ".mkv"}:
        return "video"
    return "unknown"


@router.post("/upload", response_model=FileInfo, status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    ref_description: str = Form(""),
    db: DBSession = Depends(get_db),
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

    # 保存文件到磁盘 — 强制提取纯净文件名，杜绝路径遍历攻击
    safe_filename = Path(file.filename).name
    file_id = f"f_{uuid.uuid4().hex[:8]}"
    stored_path = settings.upload_dir / file_id / safe_filename
    stored_path.parent.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    stored_path.write_bytes(content)
    size_kb = round(len(content) / 1024, 2)

    # 记录到数据库 — original_name 保留用户原始文件名用于展示
    record = FileRecord(
        file_id=file_id,
        session_id=session_id,
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
def get_file_info(file_id: str, db: DBSession = Depends(get_db)):
    f = db.query(FileRecord).filter(FileRecord.file_id == file_id).first()
    if not f:
        raise ApiError("文件不存在", code="FILE_NOT_FOUND", status_code=404)
    return FileInfo(
        file_id=f.file_id,
        original_name=f.original_name,
        file_type=f.file_type,
        size_kb=f.size_kb,
        upload_time=f.upload_time,
        ref_description=f.ref_description,
    )
