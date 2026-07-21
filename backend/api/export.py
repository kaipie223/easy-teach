"""M7 — 课件导出下载 API"""

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/download/{file_id}")
async def download_file(file_id: str):
    """
    下载生成的课件文件（pptx / docx / html 打包 zip）。
    由 M7 模块（潘卓然 + 姜文杰）实现。
    """
    # TODO: 查找文件 → 返回 FileResponse
    return {"message": f"M7 占位 — file_id={file_id}"}
