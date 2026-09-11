"""M7 — 文件下载 API"""

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DBSession

from backend.core.errors import ApiError
from backend.core.ownership import get_file_for_user
from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.models.user import User

router = APIRouter()

CONTENT_TYPES = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "html": "text/html",
    "pdf": "application/pdf",
    "image": "image/png",
}


@router.get("/download/{file_id}")
def download_file(
    file_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not file_id.strip():
        raise ApiError("file_id 不能为空", code="missing_file_id", status_code=422)

    f = get_file_for_user(db, file_id, user)

    path = Path(f.stored_path)
    if not path.exists():
        raise ApiError("文件尚未生成", code="FILE_NOT_AVAILABLE", status_code=404, details={"file_id": file_id})

    media_type = CONTENT_TYPES.get(f.file_type, "application/octet-stream")
    return FileResponse(
        path=str(path),
        media_type=media_type,
        filename=f.original_name,
    )
