"""M2 — 文件上传与多模态解析 API"""

from fastapi import APIRouter, UploadFile, File

from models.schemas import UploadResponse

router = APIRouter()


@router.post("/file", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    """
    上传 PDF/Word/图片/视频，后台解析提取文本。
    由 M2 模块（赵钰洁 + 姜文杰 + 潘卓然）实现。
    """
    # TODO: 保存文件 → 调用解析器 → 存入数据库
    return UploadResponse(
        file_id="placeholder",
        file_name=file.filename or "unknown",
        status="uploaded",
    )
