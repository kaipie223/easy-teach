"""M2 — 多模态文件解析器"""

import logging

logger = logging.getLogger(__name__)


async def parse_pdf(file_path: str) -> str:
    """解析 PDF 文件，提取文本。"""
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(file_path)
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)
    except ImportError:
        logger.warning("PyPDF2 not installed, returning placeholder")
        return f"[PDF 解析需要 PyPDF2 库: {file_path}]"


async def parse_docx(file_path: str) -> str:
    """解析 Word 文档，提取文本（保留段落结构）。"""
    try:
        from docx import Document
        doc = Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)
    except ImportError:
        logger.warning("python-docx not installed, returning placeholder")
        return f"[Word 解析需要 python-docx 库: {file_path}]"


async def parse_image(file_path: str) -> str:
    """解析图片（OCR / 多模态模型描述）。"""
    try:
        from PIL import Image
        img = Image.open(file_path)
        info = f"[图片] 尺寸: {img.size[0]}x{img.size[1]}, 模式: {img.mode}"
        # TODO: 使用多模态模型（如 DeepSeek-VL）描述图片内容
        return info
    except ImportError:
        logger.warning("Pillow not installed")
        return f"[图片解析需要 Pillow 库: {file_path}]"


async def parse_video(file_path: str) -> str:
    """解析视频（提取关键帧 + 音频转录）。"""
    try:
        import subprocess
        # 使用 ffprobe 获取视频信息
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", file_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            import json
            info = json.loads(result.stdout)
            duration = round(float(info.get("format", {}).get("duration", 0)))
            return f"[视频] 时长: {duration}秒"
        return f"[视频] 无法解析: {file_path}"
    except Exception as e:
        logger.warning("Video parsing failed: %s", e)
        return f"[视频解析失败: {file_path}]"
