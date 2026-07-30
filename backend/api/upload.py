"""M2 - File upload and multimodal parsing API."""

from uuid import uuid4

from fastapi import APIRouter, File, UploadFile

from core.errors import ApiError
from models.schemas import UploadResponse

router = APIRouter()


@router.post("/file", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    if not file.filename:
        raise ApiError("Uploaded file name is missing.", code="invalid_file", status_code=400)

    return UploadResponse(
        file_id=str(uuid4()),
        file_name=file.filename,
        status="accepted",
    )
