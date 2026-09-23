"""M5 version-bound export records and local rendering jobs."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session as DBSession

from backend.config import settings
from backend.core.errors import ApiError
from backend.models.file import FileRecord
from backend.models.project import Project
from backend.models.session import Session, gen_id
from backend.models.versioning import ArtifactVersion, ExportRecord
from backend.schemas import ExportFormat, ExportInfo
from backend.services.generator import generate_docx, generate_html, generate_pdf, generate_pptx
from backend.services.limits import ensure_storage_capacity, ensure_task_capacity
from backend.services.materials import resolve_slide_images
from backend.services.progress import EXPORT_STAGES, export_stage_label, percent_of
from backend.services.uploads import checksum_file, remove_managed_file
from backend.services.task_queue import claim_export_attempt, touch_export, utcnow
from backend.services.versions import snapshot_spec


def _safe_title(title: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", title.strip(), flags=re.UNICODE)
    value = cleaned.strip("_") or "教学课程"
    # ext4 limits a file-name component to 255 bytes. Keep ample room for the
    # version/date/export suffix even when the title consists entirely of
    # three-byte CJK characters.
    encoded = value.encode("utf-8")[:120]
    while encoded:
        try:
            return encoded.decode("utf-8").rstrip("_") or "教学课程"
        except UnicodeDecodeError:
            encoded = encoded[:-1]
    return "教学课程"


def _format_extension(export_format: str) -> str:
    return {"pptx": "pptx", "docx": "docx", "pdf": "pdf", "html": "html"}[export_format]


def export_file_name(version: ArtifactVersion, export_format: str, export_id: str) -> str:
    date_text = datetime.now(timezone.utc).strftime("%Y%m%d")
    title = _safe_title(snapshot_spec(version).title)
    extension = _format_extension(export_format)
    return f"{title}_v{version.version}_{date_text}_{export_id[-8:]}.{extension}"


def to_export_info(record: ExportRecord) -> ExportInfo:
    return ExportInfo(
        export_id=record.export_id,
        user_id=record.user_id,
        project_id=record.project_id,
        artifact_version_id=record.artifact_version_id,
        format=record.format,
        status=record.status,
        stage=record.stage,
        stage_label=export_stage_label(record.format, record.stage),
        # 已完成的记录就是 100%，不该把"最后一步 90%"留在列表里
        stage_percent=(
            100 if record.status == "completed" else percent_of(EXPORT_STAGES, record.stage)
        ),
        stage_started_at=record.stage_started_at,
        started_at=record.started_at,
        file_id=record.file_id,
        file_name=record.file_name,
        checksum_sha256=record.checksum_sha256,
        size_bytes=record.size_bytes,
        retry_count=record.retry_count or 0,
        max_retries=record.max_retries or settings.task_max_retries,
        error=record.error,
        download_url=(
            f"/api/v1/exports/{record.export_id}/download"
            if record.status == "completed" and record.path
            else None
        ),
        created_at=record.created_at,
        completed_at=record.completed_at,
    )


def create_export_records(
    db: DBSession,
    project: Project,
    version: ArtifactVersion,
    *,
    user_id: str,
    formats: list[ExportFormat],
    force: bool = False,
) -> list[ExportRecord]:
    records: list[ExportRecord] = []
    for export_format in dict.fromkeys(item.value for item in formats):
        if not force:
            existing = (
                db.query(ExportRecord)
                .filter(
                    ExportRecord.artifact_version_id == version.artifact_version_id,
                    ExportRecord.format == export_format,
                    ExportRecord.status.in_(("pending", "processing", "completed")),
                )
                .order_by(ExportRecord.created_at.desc())
                .first()
            )
            if existing is not None:
                records.append(existing)
                continue

        ensure_task_capacity(db, user_id)

        record = ExportRecord(
            user_id=user_id,
            project_id=project.project_id,
            artifact_version_id=version.artifact_version_id,
            format=export_format,
            status="pending",
            retry_count=0,
            max_retries=settings.task_max_retries,
            updated_at=utcnow(),
            created_at=datetime.now(timezone.utc),
        )
        db.add(record)
        db.flush()
        records.append(record)
    return records


def _render(
    version: ArtifactVersion,
    export_format: str,
    output_name: str,
    *,
    images: dict[str, Path] | None = None,
) -> str:
    plan = snapshot_spec(version).model_dump(mode="json")
    # The deck renders the slides themselves, while the lesson document and the
    # printed handout list the same pictures as an appendix. The interactive page
    # has no slide content, so it takes no pictures.
    if export_format == "pptx":
        return generate_pptx(plan, output_name=output_name, images=images)
    if export_format == "docx":
        return generate_docx(plan, output_name=output_name, images=images)
    if export_format == "pdf":
        return generate_pdf(plan, output_name=output_name, images=images)
    if export_format == "html":
        return generate_html(plan, output_name=output_name)
    raise ApiError(
        f"不支持的导出格式：{export_format}",
        code="EXPORT_FORMAT_UNSUPPORTED",
        status_code=422,
    )


def run_export(export_id: str, *, raise_errors: bool = False) -> None:
    """Execute one export record in an independent database session."""
    from backend.db.database import SessionLocal

    db = SessionLocal()
    try:
        record = db.query(ExportRecord).filter(ExportRecord.export_id == export_id).first()
        if record is None:
            return
        if not claim_export_attempt(db, export_id):
            return
        record = db.query(ExportRecord).filter(ExportRecord.export_id == export_id).first()
        version = (
            db.query(ArtifactVersion)
            .filter(ArtifactVersion.artifact_version_id == record.artifact_version_id)
            .first()
        )
        if version is None:
            raise RuntimeError("导出所绑定的成果版本不存在")
        session = (
            db.query(Session)
            .filter(Session.project_id == record.project_id)
            .order_by(Session.created_at.desc())
            .first()
        )
        if session is None:
            raise RuntimeError("项目尚未创建会话，无法登记导出文件")

        touch_export(db, record, "render")
        output_name = export_file_name(version, record.format, record.export_id)
        # Resolved per run so an archived or moved picture degrades to a deck
        # without that picture instead of failing the whole export job.
        images = resolve_slide_images(db, record.project_id, snapshot_spec(version))
        path = Path(_render(version, record.format, output_name, images=images))
        touch_export(db, record, "save")
        checksum, size_bytes = checksum_file(path)
        # ``exports.file_id`` is VARCHAR(40). A human-readable filename can be
        # much longer and must never double as a database identifier.
        file_id = gen_id("f")
        existing_file = db.query(FileRecord).filter(FileRecord.file_id == file_id).first()
        if existing_file is None:
            try:
                ensure_storage_capacity(db, record.user_id, size_bytes)
            except Exception:
                remove_managed_file(path, root=settings.output_dir)
                raise
            db.add(
                FileRecord(
                    file_id=file_id,
                    user_id=record.user_id,
                    project_id=record.project_id,
                    artifact_version_id=record.artifact_version_id,
                    session_id=session.session_id,
                    original_name=path.name,
                    file_type=record.format,
                    stored_path=str(path),
                    size_kb=round(path.stat().st_size / 1024, 2),
                    ref_description="version_export",
                    upload_time=datetime.now(timezone.utc),
                )
            )
        record.file_id = file_id
        record.path = str(path)
        record.file_name = path.name
        record.checksum_sha256 = checksum
        record.size_bytes = size_bytes
        record.status = "completed"
        record.completed_at = datetime.now(timezone.utc)
        record.updated_at = record.completed_at
        record.error = None
        db.commit()
    except Exception as exc:
        db.rollback()
        record = db.query(ExportRecord).filter(ExportRecord.export_id == export_id).first()
        if record is not None:
            record.status = "failed"
            record.error = str(exc)
            record.updated_at = datetime.now(timezone.utc)
            db.commit()
        if raise_errors:
            raise
    finally:
        db.close()
