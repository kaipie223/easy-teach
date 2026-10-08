"""Material ingestion and deterministic local parsing for the M3 workflow."""

from __future__ import annotations

import hashlib
import io
import logging
import mimetypes
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz
from docx import Document
from PIL import Image
from pptx import Presentation
from sqlalchemy.orm import Session as DBSession

from backend.config import settings
from backend.db.database import SessionLocal
from backend.models.material import EvidenceChunk, Material, MaterialAnalysis
from backend.schemas import CoursewarePlanSpec
from backend.services.progress import material_ai_stage
from backend.services.image_vision import (
    ImageVisionError,
    describe_image,
    description_text,
)
from video_parser.parser import PARSER_VERSION as VIDEO_PARSER_VERSION
from video_parser.parser import parse_video as video_parse_video
from video_parser.schemas import VideoParseOptions, VideoParseResult

logger = logging.getLogger(__name__)


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
    duration_seconds: int | None = None


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
VIDEO_MATERIAL_PARSER_NAME = "video-parser-model"
VIDEO_MATERIAL_PARSER_VERSION = VIDEO_PARSER_VERSION
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
    if file_type == "video" and not _looks_like_video(content):
        raise MaterialValidationError(
            "视频文件内容无效或类型不匹配",
            code="MATERIAL_TYPE_MISMATCH",
        )

    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return file_type, mime_type


def detect_file_type_from_path(filename: str, path: Path) -> tuple[str, str]:
    """Validate a staged upload without loading the complete file into memory."""
    extension = Path(filename).suffix.lower()
    file_type = FILE_TYPE_EXTENSIONS.get(extension)
    if file_type is None:
        raise MaterialValidationError(
            f"不支持的文件格式: {extension or 'unknown'}",
            code="UNSUPPORTED_MATERIAL_FORMAT",
            details={"extension": extension},
        )

    if file_type == "pdf":
        with path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise MaterialValidationError(
                    "文件扩展名与真实 PDF 类型不一致",
                    code="MATERIAL_TYPE_MISMATCH",
                )
    elif file_type in {"word", "ppt"}:
        required_member = "word/document.xml" if file_type == "word" else "ppt/presentation.xml"
        try:
            with zipfile.ZipFile(path) as archive:
                valid = required_member in archive.namelist()
        except zipfile.BadZipFile:
            valid = False
        if not valid:
            raise MaterialValidationError(
                "Office 文件内容无效或类型不匹配",
                code="MATERIAL_TYPE_MISMATCH",
            )
    elif file_type == "image":
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as exc:
            raise MaterialValidationError(
                "图片内容无法验证",
                code="MATERIAL_TYPE_MISMATCH",
            ) from exc
    elif file_type == "video":
        with path.open("rb") as source:
            header = source.read(12)
        if not _looks_like_video(header):
            raise MaterialValidationError(
                "视频文件内容无效或类型不匹配",
                code="MATERIAL_TYPE_MISMATCH",
            )

    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return file_type, mime_type


def resolve_slide_images(
    db: DBSession,
    project_id: str | None,
    plan: CoursewarePlanSpec | dict[str, Any],
) -> dict[str, Path]:
    """Map every picture a snapshot references to the file backing it.

    The snapshot is read defensively instead of validated: the legacy
    anonymous-session path passes a generation-instruction dict that is not a
    CoursewarePlan at all, and "cannot read a picture reference" has to mean
    "render no pictures" rather than "fail the export".

    References that are archived, belong to another project, or whose file has
    vanished are dropped, so a renderer never receives a path it may not embed.
    """
    if isinstance(plan, CoursewarePlanSpec):
        material_ids = {
            slide.image.material_id for slide in plan.slides if slide.image is not None
        }
    else:
        material_ids = {
            str((slide.get("image") or {}).get("material_id") or "")
            for slide in (plan.get("slides") or [])
            if isinstance(slide, dict)
        }
    material_ids.discard("")
    if not material_ids or not project_id:
        return {}

    rows = (
        db.query(Material)
        .filter(
            Material.material_id.in_(material_ids),
            Material.project_id == project_id,
            Material.deleted_at.is_(None),
            Material.file_type == "image",
        )
        .all()
    )
    resolved: dict[str, Path] = {}
    for row in rows:
        path = Path(row.stored_path)
        if path.is_file():
            resolved[row.material_id] = path
        else:
            logger.warning("Slide image is missing on disk: %s", row.material_id)
    return resolved


def parse_material(
    file_type: str,
    path: Path,
    *,
    max_chars: int = MAX_CHUNK_CHARS,
) -> ParsedMaterial:
    """Parse supported material formats into searchable, locatable evidence.

    ``max_chars`` 是分块上限。默认值服务于资料中心（参考资料只作为文本进提示词）；
    知识库会用更小的值（见 ``settings.knowledge_chunk_chars``），因为那些块要被嵌入，
    必须落在向量模型的输入窗口内。
    """
    if file_type == "pdf":
        return _parse_pdf(path, max_chars=max_chars)
    if file_type == "word":
        return _parse_docx(path, max_chars=max_chars)
    if file_type == "ppt":
        return _parse_pptx(path, max_chars=max_chars)
    if file_type == "image":
        return _parse_image(path)
    if file_type == "video":
        return _parse_video(path)
    raise MaterialValidationError("暂不支持该资料类型", code="UNSUPPORTED_MATERIAL_TYPE")


def parser_identity(file_type: str) -> tuple[str, str]:
    if file_type == "video":
        return VIDEO_MATERIAL_PARSER_NAME, VIDEO_MATERIAL_PARSER_VERSION
    return f"builtin_{file_type}", PARSER_VERSION


def apply_parsed_material(
    db,
    material: Material,
    analysis: MaterialAnalysis,
    parsed: ParsedMaterial,
) -> None:
    completed_at = datetime.now(timezone.utc)
    analysis.status = "completed"
    analysis.text_content = parsed.text_content
    analysis.result_json = parsed.result_json
    analysis.page_count = parsed.page_count
    analysis.slide_count = parsed.slide_count
    analysis.duration_seconds = parsed.duration_seconds
    analysis.error_code = None
    analysis.error_message = None
    analysis.completed_at = completed_at
    analysis.updated_at = completed_at
    material.status = "ready"
    material.error_code = None
    material.error_message = None
    material.updated_at = completed_at
    for index, chunk in enumerate(parsed.chunks):
        db.add(
            EvidenceChunk(
                evidence_id=f"evidence_{uuid.uuid4().hex[:24]}",
                material_id=material.material_id,
                analysis_id=analysis.analysis_id,
                source_type=f"uploaded_{material.file_type}",
                chunk_index=index,
                locator_json=chunk.locator,
                text=chunk.text,
                metadata_json=chunk.metadata,
                usage_tags=[],
                content_hash=checksum_sha256(chunk.text.encode("utf-8")),
                is_valid=True,
                created_at=completed_at,
            )
        )


def _touch_material(db, material: Material, stage: str) -> None:
    """Record parsing progress on the material row.

    进度放在资料上，因为资料列表返回的就是这一行；解析本身仍由 analysis 负责。
    """
    now = datetime.now(timezone.utc)
    material.stage = stage
    material.stage_started_at = now
    material.updated_at = now
    db.commit()


def fail_material_analysis(
    db,
    material: Material,
    analysis: MaterialAnalysis,
    error: Exception | str,
    *,
    code: str = "MATERIAL_PARSE_FAILED",
    status: str = "failed",
) -> None:
    now = datetime.now(timezone.utc)
    message = str(error)
    material.status = status
    material.error_code = code
    material.error_message = message
    material.updated_at = now
    analysis.status = "pending" if status == "queued" else status
    analysis.error_code = code
    analysis.error_message = message
    if status == "failed":
        analysis.completed_at = now
    analysis.updated_at = now


def _missing_video_binaries() -> list[str]:
    """视频解析依赖的外部二进制。

    缺任何一个都要等到 `probe_video` 才以 FFmpegError 炸掉，而那时的错误信息
    对使用者毫无可操作性；这里在解析真正开始前先探测一次。
    """
    return [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]


def run_material_analysis(analysis_id: str, *, raise_errors: bool = False) -> str | None:
    db = SessionLocal()
    try:
        analysis = (
            db.query(MaterialAnalysis)
            .filter(MaterialAnalysis.analysis_id == analysis_id)
            .with_for_update()
            .first()
        )
        if analysis is None or analysis.status == "completed":
            return analysis_id if analysis else None
        if analysis.status == "processing":
            # A duplicate Celery delivery must not run the same paid parser in
            # parallel. Stale jobs are explicitly moved back to pending by the
            # recovery lease before they can run again.
            return analysis_id
        material = (
            db.query(Material)
            .filter(Material.material_id == analysis.material_id)
            .with_for_update()
            .first()
        )
        if material is None or material.deleted_at is not None:
            return None
        if material.file_type == "video":
            canonical = (
                db.query(Material)
                .filter(
                    Material.owner_id == material.owner_id,
                    Material.project_id == material.project_id,
                    Material.file_type == "video",
                    Material.checksum_sha256 == material.checksum_sha256,
                    Material.deleted_at.is_(None),
                )
                .order_by(Material.created_at.asc(), Material.material_id.asc())
                .first()
            )
            if canonical is not None and canonical.material_id != material.material_id:
                fail_material_analysis(
                    db,
                    material,
                    analysis,
                    "同一视频已有解析任务，本次重复任务未执行",
                    code="VIDEO_MATERIAL_EXISTS",
                )
                db.commit()
                return analysis_id
            _require_managed_video_path(Path(material.stored_path))
        now = datetime.now(timezone.utc)
        material.status = "processing"
        material.error_code = None
        material.error_message = None
        material.updated_at = now
        analysis.status = "processing"
        analysis.started_at = analysis.started_at or now
        analysis.completed_at = None
        analysis.error_code = None
        analysis.error_message = None
        analysis.updated_at = now
        db.commit()

        if material.file_type == "video":
            missing = _missing_video_binaries()
            if missing:
                raise MaterialValidationError(
                    f"视频解析依赖 {'、'.join(missing)}，但服务器上找不到；"
                    "请在部署环境安装 ffmpeg（需包含 ffprobe）后重试",
                    code="FFMPEG_NOT_AVAILABLE",
                )

        _touch_material(db, material, "extract")
        # 图片与视频的模型调用是这条链路里唯一可能跑几分钟的一步，必须在开始等待
        # 之前就把阶段推过去，否则进度条会一直停在"提取文件内容"。
        ai_stage = material_ai_stage(material.file_type)
        if ai_stage is not None:
            _touch_material(db, material, ai_stage)

        parsed = parse_material(material.file_type, Path(material.stored_path))

        analysis = db.query(MaterialAnalysis).filter(MaterialAnalysis.analysis_id == analysis_id).one()
        material = db.query(Material).filter(Material.material_id == analysis.material_id).one()
        db.query(EvidenceChunk).filter(EvidenceChunk.analysis_id == analysis.analysis_id).delete()
        _touch_material(db, material, "index")
        apply_parsed_material(db, material, analysis, parsed)
        db.commit()
        return analysis_id
    except Exception as exc:
        db.rollback()
        analysis = db.query(MaterialAnalysis).filter(MaterialAnalysis.analysis_id == analysis_id).first()
        material = (
            db.query(Material).filter(Material.material_id == analysis.material_id).first()
            if analysis is not None
            else None
        )
        if analysis is not None and material is not None:
            error_code = exc.code if isinstance(exc, MaterialValidationError) else "MATERIAL_PARSE_FAILED"
            fail_material_analysis(db, material, analysis, exc, code=error_code)
            db.commit()
        if raise_errors:
            raise
        return None
    finally:
        db.close()


def prepare_material_retry(analysis_id: str, error: Exception | str) -> bool:
    db = SessionLocal()
    try:
        analysis = db.query(MaterialAnalysis).filter(MaterialAnalysis.analysis_id == analysis_id).first()
        material = (
            db.query(Material).filter(Material.material_id == analysis.material_id).first()
            if analysis is not None
            else None
        )
        if analysis is None or material is None or analysis.status == "completed":
            return False
        fail_material_analysis(
            db,
            material,
            analysis,
            error,
            code="MATERIAL_PARSE_RETRYING",
            status="queued",
        )
        db.commit()
        return True
    finally:
        db.close()


def _zip_contains(content: bytes, member: str) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            return member in archive.namelist()
    except zipfile.BadZipFile:
        return False


def _looks_like_video(content: bytes) -> bool:
    if len(content) < 12:
        return False
    if content.startswith(b"\x1a\x45\xdf\xa3"):
        return True
    if content.startswith(b"RIFF") and content[8:12] == b"AVI ":
        return True
    return content[4:8] == b"ftyp"


def _parse_pdf(path: Path, *, max_chars: int = MAX_CHUNK_CHARS) -> ParsedMaterial:
    chunks: list[ParsedChunk] = []
    pages: list[str] = []
    # 有字的页数单独计数：整本扫描件（一页都抽不出字）导入后会"成功但零块"，
    # 只靠 chunk_count 看不出来，抽字率才是能一眼发现问题的那项指标。
    text_pages = 0
    with fitz.open(path) as document:
        for page_number, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            if not text:
                continue
            text_pages += 1
            pages.append(text)
            chunks.extend(
                _chunks(text, {"page": page_number}, {"format": "pdf"}, max_chars=max_chars)
            )
        page_count = len(document)
    return ParsedMaterial(
        text_content="\n\n".join(pages),
        chunks=chunks,
        result_json={
            "format": "pdf",
            "page_count": page_count,
            "text_pages": text_pages,
            "chunk_count": len(chunks),
        },
        page_count=page_count,
    )


def _parse_docx(path: Path, *, max_chars: int = MAX_CHUNK_CHARS) -> ParsedMaterial:
    document = Document(path)
    chunks: list[ParsedChunk] = []
    blocks: list[str] = []
    for index, paragraph in enumerate(document.paragraphs, start=1):
        text = paragraph.text.strip()
        if text:
            blocks.append(text)
            chunks.extend(
                _chunks(text, {"paragraph": index}, {"format": "docx"}, max_chars=max_chars)
            )

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
                    max_chars=max_chars,
                )
            )

    return ParsedMaterial(
        text_content="\n\n".join(blocks),
        chunks=chunks,
        result_json={"format": "docx", "paragraph_count": len(document.paragraphs), "chunk_count": len(chunks)},
    )


def _parse_pptx(path: Path, *, max_chars: int = MAX_CHUNK_CHARS) -> ParsedMaterial:
    presentation = Presentation(path)
    chunks: list[ParsedChunk] = []
    slides: list[str] = []
    for slide_number, slide in enumerate(presentation.slides, start=1):
        text_blocks = [shape.text.strip() for shape in slide.shapes if hasattr(shape, "text")]
        text = "\n".join(block for block in text_blocks if block)
        if not text:
            continue
        slides.append(text)
        chunks.extend(
            _chunks(text, {"slide": slide_number}, {"format": "pptx"}, max_chars=max_chars)
        )
    return ParsedMaterial(
        text_content="\n\n".join(slides),
        chunks=chunks,
        result_json={"format": "pptx", "slide_count": len(presentation.slides), "chunk_count": len(chunks)},
        slide_count=len(presentation.slides),
    )


def _parse_image(path: Path) -> ParsedMaterial:
    """Describe a picture so the blueprint model can actually use it.

    Without a configured vision model this keeps the previous behaviour exactly:
    the file is stored, the analysis states that understanding is unavailable, and
    no text is invented. A configured provider that fails is reported as failed
    for the same reason — a picture nobody read must never look like a picture
    somebody read.
    """
    with Image.open(path) as image:
        metadata: dict[str, Any] = {
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            "requires_vision": True,
        }

    try:
        description = describe_image(path)
    except ImageVisionError as exc:
        logger.warning("Image understanding failed: %s", exc)
        metadata.update({"vision_status": "failed", "warning": str(exc)})
        return ParsedMaterial(text_content="", chunks=[], result_json=metadata)

    if description is None:
        metadata.update(
            {
                "vision_status": "not_configured",
                "warning": "图片已保存，但视觉识别模型尚未配置，未生成证据文本。",
            }
        )
        return ParsedMaterial(text_content="", chunks=[], result_json=metadata)

    text = description_text(description)
    metadata.update(
        {
            "vision_status": "ready",
            "description": description["description"],
            "keywords": description["keywords"],
            "suggested_use": description["suggested_use"],
            "vision_model": description["model_name"],
            "vision_prompt_version": description["prompt_version"],
        }
    )
    return ParsedMaterial(
        text_content=text,
        chunks=_chunks(
            text,
            {"kind": "image_description"},
            {"format": metadata["format"], "vision_status": "ready"},
        ),
        result_json=metadata,
    )


def list_project_images(db: DBSession, project_id: str | None) -> list[dict[str, Any]]:
    """Pictures this project uploaded, described well enough for the model to pick one.

    This is what makes "the AI chooses the pictures" possible: the blueprint model
    cannot see the files, so it is handed these descriptions and may only reference
    the IDs listed here.
    """
    if not project_id:
        return []
    rows = (
        db.query(Material)
        .filter(
            Material.project_id == project_id,
            Material.file_type == "image",
            Material.deleted_at.is_(None),
        )
        .order_by(Material.created_at.asc())
        .all()
    )
    images: list[dict[str, Any]] = []
    for material in rows:
        analysis = (
            db.query(MaterialAnalysis)
            .filter(MaterialAnalysis.material_id == material.material_id)
            .order_by(MaterialAnalysis.run_number.desc())
            .first()
        )
        result = (analysis.result_json if analysis is not None else None) or {}
        images.append(
            {
                "material_id": material.material_id,
                "name": material.original_name,
                # The teacher's own note is the fallback signal when vision is off.
                "teacher_note": (material.ref_description or "").strip(),
                "description": str(result.get("description") or ""),
                "keywords": [str(item) for item in (result.get("keywords") or [])],
                "vision_status": str(result.get("vision_status") or "pending"),
            }
        )
    return images


def _parse_video(path: Path) -> ParsedMaterial:
    options = VideoParseOptions(
        video_type=_video_type(),
        transcribe=settings.video_parser_transcribe,
        ocr=settings.video_parser_ocr,
        vision=settings.video_parser_vision,
        video_understanding=settings.video_parser_understanding,
        max_sample_keyframes=settings.video_parser_max_keyframes,
        max_shots=settings.video_parser_max_shots,
        output_width=settings.video_parser_output_width,
        vision_max_keyframes=settings.video_parser_vision_max_keyframes,
        bailian_api_key_file=settings.bailian_api_key_file,
        dashscope_base_url=settings.dashscope_base_url,
        video_model=settings.video_model,
        video_input_mode=settings.video_input_mode,
        video_fps=settings.video_fps,
        video_max_frames=settings.video_max_frames,
        video_chunk_seconds=settings.video_chunk_seconds,
        video_chunk_overlap_seconds=settings.video_chunk_overlap_seconds,
        video_max_chunks=settings.video_max_chunks,
        video_max_duration_seconds=settings.video_parser_max_duration_seconds,
        video_timeout_seconds=settings.video_timeout_seconds,
        video_max_retries=settings.video_max_retries,
        video_max_output_tokens=settings.video_max_output_tokens,
        video_max_base64_bytes=settings.video_max_base64_bytes,
        video_strict_schema=settings.video_strict_schema,
        video_cache_enabled=settings.video_cache_enabled,
        video_prompt_version=settings.video_prompt_version,
        video_schema_version=settings.video_schema_version,
        video_max_refinement_intervals=settings.video_max_refinement_intervals,
        video_max_refinement_frames=settings.video_max_refinement_frames,
        video_max_visual_frames_per_interval=settings.video_max_visual_frames_per_interval,
        vision_model=settings.bailian_vision_model,
        vision_timeout_seconds=settings.bailian_vision_timeout_seconds,
        vision_max_retries=settings.bailian_vision_max_retries,
        vision_max_output_tokens=settings.bailian_vision_max_output_tokens,
    )
    result = video_parse_video(path, output_root=settings.video_parser_output_dir, options=options)
    return _video_result_to_material(result)


def _video_type() -> str:
    if settings.video_parser_type in {"auto", "presentation", "whiteboard", "operation"}:
        return settings.video_parser_type
    return "auto"


def _video_result_to_material(result: VideoParseResult) -> ParsedMaterial:
    chunks = [_video_evidence_to_chunk(item) for item in result.evidence if item.content.strip()]
    chunks.extend(_video_understanding_candidate_chunks(result))
    segment_lines = [
        f"{segment.time_range.start}-{segment.time_range.end} {segment.summary}"
        for segment in result.segments
        if segment.summary.strip()
    ]
    text_parts = [
        result.transcript.text.strip(),
        *segment_lines,
        *(item.text for item in chunks if item.text.strip()),
    ]
    payload = result.model_dump(mode="json")
    return ParsedMaterial(
        text_content="\n".join(dict.fromkeys(item for item in text_parts if item)),
        chunks=chunks,
        result_json={
            "format": "video",
            "parser": {"name": VIDEO_MATERIAL_PARSER_NAME, "version": VIDEO_MATERIAL_PARSER_VERSION},
            "video_id": result.video_id,
            "duration_seconds": result.metadata.duration_seconds,
            "segment_count": len(result.segments),
            "keyframe_count": len(result.keyframes),
            "evidence_count": len(chunks),
            "parser_evidence_count": len(result.evidence),
            "ai_candidate_evidence_count": sum(
                1 for item in chunks if item.metadata.get("evidence_nature") == "ai_inference"
            ),
            "warnings": result.warnings,
            "artifacts": result.artifacts,
            "result": payload,
        },
        duration_seconds=round(result.metadata.duration_seconds),
    )


def _video_evidence_to_chunk(item) -> ParsedChunk:
    time_range = item.time_range.model_dump(mode="json") if item.time_range else None
    locator: dict[str, Any] = {
        "source_id": item.source_id,
        "evidence_type": item.evidence_type,
    }
    if time_range:
        locator["timestamp"] = time_range.get("start")
        locator["time_range"] = time_range
    metadata = {
        "format": "video",
        "evidence_type": item.evidence_type,
        "source_path": item.source_path,
        **(item.metadata or {}),
    }
    return ParsedChunk(text=item.content, locator=locator, metadata=metadata)


def _video_understanding_candidate_chunks(result: VideoParseResult) -> list[ParsedChunk]:
    """Project validated, time-aligned model candidates into reviewable search chunks.

    These chunks intentionally remain marked as AI inference. They make a
    visual-only video discoverable by downstream retrieval without presenting
    the model's interpretation as an observed source fact.
    """

    understanding = result.video_understanding
    if understanding is None or understanding.status not in {"completed", "partial"}:
        return []
    decisions = {item.candidate_id: item for item in understanding.alignment_decisions}
    conflicts_by_candidate: dict[str, list[str]] = {}
    for conflict in result.conflicts:
        if conflict.candidate_id:
            conflicts_by_candidate.setdefault(conflict.candidate_id, []).append(conflict.id)

    provenance = understanding.provenance.model_dump(mode="json") if understanding.provenance else {}
    chunks: list[ParsedChunk] = []
    for chapter in understanding.chapters:
        decision = decisions.get(chapter.chapter_id)
        if decision is None or decision.status == "rejected":
            continue
        knowledge_lines = [
            f"{item.title}：{item.description}" if item.description.strip() else item.title
            for item in chapter.knowledge_points
            if item.title.strip()
        ]
        content_parts = [
            "AI 视频理解候选（待教师复核）",
            f"章节：{chapter.title.strip()}",
            f"摘要：{chapter.summary.strip()}" if chapter.summary.strip() else "",
            "知识点候选：" + "；".join(knowledge_lines) if knowledge_lines else "",
        ]
        content = "\n".join(item for item in content_parts if item)
        if not content.strip():
            continue
        start_seconds = decision.aligned_start_seconds
        end_seconds = decision.aligned_end_seconds
        locator = {
            "source_id": chapter.chapter_id,
            "evidence_type": "derived",
            "timestamp": _video_timecode(start_seconds),
            "time_range": {
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "start": _video_timecode(start_seconds),
                "end": _video_timecode(end_seconds),
            },
        }
        metadata = {
            "format": "video",
            "evidence_type": "derived",
            "source_layer": "video_understanding_candidate",
            "evidence_nature": "ai_inference",
            "ai_generated": True,
            "structure_validated": True,
            "time_aligned": True,
            "confidence": chapter.confidence,
            "human_review_status": "pending",
            "human_review_required": True,
            "must_not_be_presented_as_observed_fact": True,
            "alignment": decision.model_dump(mode="json"),
            "conflict_ids": conflicts_by_candidate.get(chapter.chapter_id, []),
            "provenance": provenance,
        }
        chunks.extend(_chunks(content, locator, metadata))
    return chunks


def _video_timecode(seconds: float) -> str:
    milliseconds = max(0, round(float(seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def _require_managed_video_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    upload_root = settings.upload_dir.expanduser().resolve()
    try:
        resolved.relative_to(upload_root)
    except ValueError as exc:
        raise MaterialValidationError(
            "视频来源不在受管上传目录内",
            code="VIDEO_SOURCE_PATH_INVALID",
        ) from exc
    if not resolved.is_file():
        raise MaterialValidationError(
            "视频源文件不存在",
            code="MATERIAL_FILE_NOT_FOUND",
        )
    return resolved


def _chunks(
    text: str,
    locator: dict,
    metadata: dict,
    *,
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[ParsedChunk]:
    normalized = " ".join(text.split())
    if not normalized:
        return []
    parts = [normalized[index : index + max_chars] for index in range(0, len(normalized), max_chars)]
    return [
        ParsedChunk(
            text=part,
            locator={**locator, "part": index + 1} if len(parts) > 1 else locator,
            metadata=metadata,
        )
        for index, part in enumerate(parts)
    ]
