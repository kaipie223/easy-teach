"""M5 revision, immutable version and version-bound export APIs."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DBSession

from backend.core.errors import ApiError
from backend.core.ownership import get_project_for_user
from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.models.project import Project
from backend.models.user import User
from backend.models.versioning import ArtifactVersion, ExportRecord, RevisionPatch
from backend.schemas import (
    ArtifactVersionInfo,
    AIRegenerateRequest,
    ExportBatchInfo,
    ExportCreateRequest,
    ExportInfo,
    RevisionApplyRequest,
    RevisionInterpretRequest,
    RevisionPatchInfo,
    RestoreVersionRequest,
)
from backend.services.exports import create_export_records, run_export, to_export_info  # noqa: F401
from backend.services.limits import consume_model_quota
from backend.services.revision_ai import RevisionAIError, find_target, regenerate_target
from backend.services.task_queue import enqueue_export
from backend.services.versions import (
    apply_operations,
    apply_patch,
    create_patch,
    create_version,
    get_latest_version,
    get_version,
    interpret_instruction,
    restore_version,
    snapshot_spec,
    to_patch_info,
    to_version_info,
)

project_router = APIRouter()
export_router = APIRouter()


def _version_or_404(db: DBSession, project: Project, version_id: str) -> ArtifactVersion:
    version = get_version(db, project.project_id, version_id)
    if version is None:
        raise ApiError("成果版本不存在", code="VERSION_NOT_FOUND", status_code=404)
    return version


def _revision_ai_api_error(error: RevisionAIError) -> ApiError:
    status_codes = {
        "REVISION_TARGET_NOT_FOUND": 422,
        "AI_NOT_CONFIGURED": 503,
        "AI_RATE_LIMITED": 429,
        "AI_TIMEOUT": 504,
        "AI_CONNECTION_FAILED": 502,
        "AI_PROVIDER_ERROR": 502,
        "AI_INVALID_RESPONSE": 502,
    }
    return ApiError(
        str(error),
        code=error.code,
        status_code=status_codes.get(error.code, 500),
        recoverable=error.recoverable,
        suggested_action="请刷新成果版本后重试" if error.recoverable else None,
    )


@project_router.get("/{project_id}/versions", response_model=list[ArtifactVersionInfo])
def list_versions(
    project_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    versions = (
        db.query(ArtifactVersion)
        .filter(ArtifactVersion.project_id == project.project_id)
        .order_by(ArtifactVersion.version.desc(), ArtifactVersion.created_at.desc())
        .all()
    )
    return [to_version_info(version) for version in versions]


@project_router.get(
    "/{project_id}/versions/{version_id}", response_model=ArtifactVersionInfo
)
def get_version_detail(
    project_id: str,
    version_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    return to_version_info(_version_or_404(db, project, version_id))


@project_router.post(
    "/{project_id}/revisions/interpret",
    response_model=RevisionPatchInfo,
    status_code=201,
)
def interpret_revision(
    project_id: str,
    request: RevisionInterpretRequest,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    base = (
        _version_or_404(db, project, request.base_version_id)
        if request.base_version_id
        else get_latest_version(db, project.project_id)
    )
    if base is None:
        raise ApiError(
            "项目尚未生成成果版本",
            code="VERSION_NOT_FOUND",
            status_code=409,
            suggested_action="先生成一份成果，再提交局部修改",
        )

    scope, target_ids, operations, cascade_check, requires_confirmation, summary = interpret_instruction(
        snapshot_spec(base), request.instruction
    )
    # Validate the preview before persisting it. The base snapshot remains untouched.
    apply_operations(snapshot_spec(base), operations)
    patch = create_patch(
        db,
        user_id=user.user_id,
        project_id=project.project_id,
        base_version_id=base.artifact_version_id,
        instruction=request.instruction,
        scope=scope,
        target_ids=target_ids,
        operations=operations,
        cascade_check=cascade_check,
        requires_confirmation=requires_confirmation,
        summary=summary,
    )
    db.commit()
    db.refresh(patch)
    return to_patch_info(patch)


@project_router.post(
    "/{project_id}/revisions/apply",
    response_model=ArtifactVersionInfo,
    status_code=201,
)
def apply_revision(
    project_id: str,
    request: RevisionApplyRequest,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    patch = (
        db.query(RevisionPatch)
        .filter(
            RevisionPatch.patch_id == request.patch_id,
            RevisionPatch.project_id == project.project_id,
            RevisionPatch.user_id == user.user_id,
        )
        .first()
    )
    if patch is None:
        raise ApiError("修改 Patch 不存在", code="REVISION_PATCH_NOT_FOUND", status_code=404)
    if patch.requires_confirmation and not request.confirmed:
        raise ApiError(
            "该修改影响范围较大，需要确认后执行",
            code="REVISION_CONFIRMATION_REQUIRED",
            status_code=409,
            suggested_action="确认 Patch 的目标和级联影响后再执行",
        )
    version = apply_patch(db, project, patch, user_id=user.user_id)
    db.commit()
    db.refresh(version)
    return to_version_info(version)


@project_router.post(
    "/{project_id}/revisions/regenerate",
    response_model=ArtifactVersionInfo,
    status_code=201,
)
async def regenerate_revision_target(
    project_id: str,
    request: AIRegenerateRequest,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    base = _version_or_404(db, project, request.base_version_id)
    current = get_latest_version(db, project.project_id)
    if current is None or current.artifact_version_id != base.artifact_version_id:
        raise ApiError(
            "成果版本已变化，请基于最新版本重新生成",
            code="VERSION_CONFLICT",
            status_code=409,
            details={
                "expected": base.artifact_version_id,
                "current": current.artifact_version_id if current else None,
            },
        )

    snapshot = snapshot_spec(base)
    try:
        find_target(snapshot, request.target_type, request.target_id)
    except RevisionAIError as error:
        raise _revision_ai_api_error(error) from error
    consume_model_quota(user.user_id)
    try:
        result = await asyncio.to_thread(
            regenerate_target,
            snapshot,
            target_type=request.target_type,
            target_id=request.target_id,
            instruction=request.instruction,
        )
    except RevisionAIError as error:
        raise _revision_ai_api_error(error) from error

    version = create_version(
        db,
        project,
        user_id=user.user_id,
        source_plan_id=base.source_plan_id,
        snapshot=result.spec,
        base_version_id=base.artifact_version_id,
        summary=f"AI 局部重生成 {request.target_id}：{request.instruction.strip()[:160]}",
        generation_mode="ai",
        model_name=result.model_name,
        prompt_version=result.prompt_version,
        usage=result.usage,
        expected_latest_version_id=base.artifact_version_id,
    )
    version.quality_status = result.quality_report["status"]
    version.quality_report = result.quality_report
    db.commit()
    db.refresh(version)
    return to_version_info(version)


@project_router.post(
    "/{project_id}/versions/{version_id}/restore",
    response_model=ArtifactVersionInfo,
    status_code=201,
)
def restore_artifact_version(
    project_id: str,
    version_id: str,
    request: RestoreVersionRequest | None = None,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    target = _version_or_404(db, project, version_id)
    version = restore_version(
        db,
        project,
        target,
        user_id=user.user_id,
        summary=request.summary if request else None,
    )
    db.commit()
    db.refresh(version)
    return to_version_info(version)


@project_router.post(
    "/{project_id}/exports",
    response_model=ExportBatchInfo,
    status_code=202,
)
def create_exports(
    project_id: str,
    request: ExportCreateRequest,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    version = (
        _version_or_404(db, project, request.artifact_version_id)
        if request.artifact_version_id
        else get_latest_version(db, project.project_id)
    )
    if version is None:
        raise ApiError(
            "项目尚未生成成果版本",
            code="VERSION_NOT_FOUND",
            status_code=409,
            suggested_action="先生成一份成果，再创建导出任务",
        )
    records = create_export_records(
        db,
        project,
        version,
        user_id=user.user_id,
        formats=request.formats,
        force=request.force,
    )
    db.commit()
    for record in records:
        if record.status == "pending":
            enqueue_export(record.export_id, db=db)
    return ExportBatchInfo(exports=[to_export_info(record) for record in records])


@project_router.get("/{project_id}/exports", response_model=list[ExportInfo])
def list_project_exports(
    project_id: str,
    artifact_version_id: str | None = Query(default=None),
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    query = db.query(ExportRecord).filter(ExportRecord.project_id == project.project_id)
    if artifact_version_id:
        query = query.filter(ExportRecord.artifact_version_id == artifact_version_id)
    records = query.order_by(ExportRecord.created_at.desc()).all()
    return [to_export_info(record) for record in records]


def _export_for_user(db: DBSession, export_id: str, user: User) -> ExportRecord:
    query = db.query(ExportRecord).filter(
        ExportRecord.export_id == export_id,
        ExportRecord.user_id == user.user_id,
    )
    record = query.first()
    if record is None:
        raise ApiError("导出记录不存在", code="EXPORT_NOT_FOUND", status_code=404)
    return record


@export_router.get("/{export_id}", response_model=ExportInfo)
def get_export(
    export_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return to_export_info(_export_for_user(db, export_id, user))


@export_router.get("/{export_id}/download")
def download_export(
    export_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    record = _export_for_user(db, export_id, user)
    if record.status != "completed" or not record.path:
        raise ApiError(
            "导出文件尚未完成",
            code="EXPORT_NOT_READY",
            status_code=409,
            suggested_action="等待导出任务完成后重试",
        )
    path = Path(record.path)
    if not path.is_file():
        raise ApiError("导出文件不存在", code="EXPORT_FILE_NOT_FOUND", status_code=404)
    media_type = {
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
        "html": "text/html",
    }.get(record.format, "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=record.file_name)
