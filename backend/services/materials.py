"""Material ingestion and deterministic local parsing for the M3 workflow."""

from __future__ import annotations

import hashlib
import io
import mimetypes
import zipfile
from dataclasses import dataclass
from pathlib import Path

import fitz
from docx import Document
from PIL import Image
from pptx import Presentation


class MaterialValidationError(ValueError):
    """Raised when an uploaded file fails type or content validation."""

    def __init__(self, message: str, *, code: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class ParsedChunk:
    text: str
    locator: dict
    metadata: dict


@dataclass(frozen=True)
class ParsedMaterial:
    text_content: str
    chunks: list[ParsedChunk]
    result_json: dict
    page_count: int | None = None
    slide_count: int | None = None


FILE_TYPE_EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "word",
    ".pptx": "ppt",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".gif": "image",
    ".bmp": "image",
    ".webp": "image",
    ".mp4": "video",
    ".avi": "video",
    ".mov": "video",
    ".mkv": "video",
}

PARSER_VERSION = "builtin-1"
MAX_CHUNK_CHARS = 1200


def checksum_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def detect_file_type(filename: str, content: bytes) -> tuple[str, str]:
    """Validate the extension against a small set of content signatures."""
    extension = Path(filename).suffix.lower()
    file_type = FILE_TYPE_EXTENSIONS.get(extension)
    if file_type is None:
        raise MaterialValidationError(
            f"不支持的文件格式: {extension or 'unknown'}",
            code="UNSUPPORTED_MATERIAL_FORMAT",
            details={"extension": extension},
        )

    if file_type == "pdf" and not content.startswith(b"%PDF-"):
        raise MaterialValidationError(
            "文件扩展名与真实 PDF 类型不一致",
            code="MATERIAL_TYPE_MISMATCH",
        )

    if file_type in {"word", "ppt"}:
        required_member = "word/document.xml" if file_type == "word" else "ppt/presentation.xml"
        if not _zip_contains(content, required_member):
            raise MaterialValidationError(
                "Office 文件内容无效或类型不匹配",
                code="MATERIAL_TYPE_MISMATCH",
            )

    if file_type == "image":
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
        except Exception as exc:
            raise MaterialValidationError(
                "图片内容无法验证",
                code="MATERIAL_TYPE_MISMATCH",
            ) from exc

    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return file_type, mime_type


def parse_material(file_type: str, path: Path) -> ParsedMaterial:
    """Parse supported non-video formats without requiring an external model."""
    if file_type == "pdf":
        return _parse_pdf(path)
    if file_type == "word":
        return _parse_docx(path)
    if file_type == "ppt":
        return _parse_pptx(path)
    if file_type == "image":
        return _parse_image(path)
    if file_type == "video":
        raise MaterialValidationError(
            "视频解析将在后续阶段启用",
            code="VIDEO_PARSING_DEFERRED",
        )
    raise MaterialValidationError("暂不支持该资料类型", code="UNSUPPORTED_MATERIAL_TYPE")


def _zip_contains(content: bytes, member: str) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            return member in archive.namelist()
    except zipfile.BadZipFile:
        return False


def _parse_pdf(path: Path) -> ParsedMaterial:
    chunks: list[ParsedChunk] = []
    pages: list[str] = []
    with fitz.open(path) as document:
        for page_number, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            if not text:
                continue
            pages.append(text)
            chunks.extend(_chunks(text, {"page": page_number}, {"format": "pdf"}))
        page_count = len(document)
    return ParsedMaterial(
        text_content="\n\n".join(pages),
        chunks=chunks,
        result_json={"format": "pdf", "page_count": page_count, "chunk_count": len(chunks)},
        page_count=page_count,
    )


def _parse_docx(path: Path) -> ParsedMaterial:
    document = Document(path)
    chunks: list[ParsedChunk] = []
    blocks: list[str] = []
    for index, paragraph in enumerate(document.paragraphs, start=1):
        text = paragraph.text.strip()
        if text:
            blocks.append(text)
            chunks.extend(_chunks(text, {"paragraph": index}, {"format": "docx"}))

    for table_index, table in enumerate(document.tables, start=1):
        rows = [" | ".join(cell.text.strip().replace("\n", " ") for cell in row.cells) for row in table.rows]
        table_text = "\n".join(row for row in rows if row.strip())
        if table_text:
            blocks.append(table_text)
            chunks.extend(
                _chunks(
                    table_text,
                    {"table": table_index},
                    {"format": "docx", "content_type": "table"},
                )
            )

    return ParsedMaterial(
        text_content="\n\n".join(blocks),
        chunks=chunks,
        result_json={"format": "docx", "paragraph_count": len(document.paragraphs), "chunk_count": len(chunks)},
    )


def _parse_pptx(path: Path) -> ParsedMaterial:
    presentation = Presentation(path)
    chunks: list[ParsedChunk] = []
    slides: list[str] = []
    for slide_number, slide in enumerate(presentation.slides, start=1):
        text_blocks = [shape.text.strip() for shape in slide.shapes if hasattr(shape, "text")]
        text = "\n".join(block for block in text_blocks if block)
        if not text:
            continue
        slides.append(text)
        chunks.extend(_chunks(text, {"slide": slide_number}, {"format": "pptx"}))
    return ParsedMaterial(
        text_content="\n\n".join(slides),
        chunks=chunks,
        result_json={"format": "pptx", "slide_count": len(presentation.slides), "chunk_count": len(chunks)},
        slide_count=len(presentation.slides),
    )


def _parse_image(path: Path) -> ParsedMaterial:
    with Image.open(path) as image:
        metadata = {
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            "requires_vision": True,
        }
    return ParsedMaterial(text_content="", chunks=[], result_json=metadata)


def _chunks(text: str, locator: dict, metadata: dict) -> list[ParsedChunk]:
    normalized = " ".join(text.split())
    if not normalized:
        return []
    parts = [normalized[index : index + MAX_CHUNK_CHARS] for index in range(0, len(normalized), MAX_CHUNK_CHARS)]
    return [
        ParsedChunk(
            text=part,
            locator={**locator, "part": index + 1} if len(parts) > 1 else locator,
            metadata=metadata,
        )
        for index, part in enumerate(parts)
    ]
