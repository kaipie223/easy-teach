"""M2 — 多模态文件解析器"""


async def parse_pdf(file_path: str) -> str:
    """解析 PDF 文件，提取文本"""
    raise NotImplementedError("待赵钰洁实现")


async def parse_docx(file_path: str) -> str:
    """解析 Word 文档，提取文本"""
    raise NotImplementedError("待赵钰洁实现")


async def parse_image(file_path: str) -> str:
    """解析图片（OCR / 多模态模型描述）"""
    raise NotImplementedError("待赵钰洁实现")


async def parse_video(file_path: str) -> str:
    """解析视频（提取关键帧 + 音频转录）"""
    raise NotImplementedError("待赵钰洁实现")
