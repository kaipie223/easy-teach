"""M7 - Generated asset download API."""

from fastapi import APIRouter

from core.errors import ApiError

router = APIRouter()


@router.get("/download/{file_id}")
async def download_file(file_id: str):
    if not file_id.strip():
        raise ApiError("file_id is required.", code="missing_file_id", status_code=422)

    raise ApiError(
        "Requested file is not available yet.",
        code="file_not_found",
        status_code=404,
        details={"file_id": file_id},
    )
