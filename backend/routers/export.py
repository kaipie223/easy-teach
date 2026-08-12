"""M7 — 文件下载 API"""

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DBSession

from core.errors import ApiError
from db.database import get_db
from models.file import FileRecord
from models.task import Task

router = APIRouter()

CONTENT_TYPES = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "html": "text/html",
    "pdf": "application/pdf",
    "image": "image/png",
}


@router.get("/download/{file_id}")
def download_file(file_id: str, db: DBSession = Depends(get_db)):
    if not file_id.strip():
        raise ApiError("file_id 不能为空", code="missing_file_id", status_code=422)

    f = db.query(FileRecord).filter(FileRecord.file_id == file_id).first()
    if not f:
        raise ApiError("文件不存在", code="FILE_NOT_FOUND", status_code=404, details={"file_id": file_id})

    path = Path(f.stored_path)
    if not path.exists():
        raise ApiError("文件尚未生成", code="FILE_NOT_AVAILABLE", status_code=404, details={"file_id": file_id})

    media_type = CONTENT_TYPES.get(f.file_type, "application/octet-stream")
    return FileResponse(
        path=str(path),
        media_type=media_type,
        filename=f.original_name,
    )
