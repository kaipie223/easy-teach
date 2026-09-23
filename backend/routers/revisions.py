"""M5 revision, immutable version and version-bound export APIs."""

import asyncio
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session as DBSession

from backend.core.errors import ApiError
from backend.core.ownership import get_material_for_user, get_project_for_user
from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.models.material import Material
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
    SlideImageRequest,
)
from backend.services.ai_stream import aiter_threaded_producer
from backend.services.exports import create_export_records, run_export, to_export_info  # noqa: F401
from backend.services.image_generation import image_generation_enabled
from backend.services.limits import consume_model_quota
from backend.services.materials import list_project_images
from backend.services.slide_illustration import illustrate_slides
from backend.services.revision_ai import (
    RevisionAIError,
    find_target,
    regenerate_target,
    resolve_edit_scope,
)
from backend.services.sse import (
    SSE_HEADERS,
    SSE_MEDIA_TYPE,
    encode_sse,
    error_frame,
    wants_event_stream,
)
from backend.services.progress import REVISION_BRANCHES, REVISION_STAGES, frame
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
logger = logging.getLogger(__name__)




def _version_or_404(db: DBSession, project: Project, version_id: str) -> ArtifactVersion:
    version = get_version(db, project.project_id, version_id)
    if version is None:
        raise ApiError("成果版本不存在", code="VERSION_NOT_FOUND", status_code=404)
    return version


SLIDE_PLACEMENT_LABELS = {
    "right": "右侧图文",
    "full": "整页大图",
    "background": "背景图",
}


def _project_image(db: DBSession, project: Project, user: User, material_id: str) -> Material:
    """Resolve a picture that may legally be attached to this project's deck.

    A picture is a private upload, so this is the only place a reference can be
    created and it verifies the material is an image of *this* project that is
    still present on disk. Rendering re-checks ownership as well, so a snapshot
    that was hand-edited or whose material was deleted afterwards degrades to
    "no picture" instead of embedding another project's file.
    """
    material = get_material_for_user(db, material_id, user)
    if material.file_type != "image":
        raise ApiError(
            "只能选用图片资料作为配图",
            code="SLIDE_IMAGE_NOT_AN_IMAGE",
            status_code=422,
            details={"file_type": material.file_type},
        )
    if material.project_id != project.project_id:
        raise ApiError(
            "只能选用本项目上传的图片",
            code="SLIDE_IMAGE_OTHER_PROJECT",
            status_code=422,
            suggested_action="请先在本项目的「资料」页上传该图片",
        )
    if not Path(material.stored_path).is_file():
        raise ApiError(
            "图片文件已丢失，无法作为配图",
            code="SLIDE_IMAGE_FILE_MISSING",
            status_code=422,
            suggested_action="请删除该资料后重新上传，再设置配图",
        )
    return material


def _anchor_label(request: AIRegenerateRequest) -> str:
    """Readable anchor for the version summary shown in the history list."""
    if request.field is None:
        return request.target_id
    if request.index is None:
        return f"{request.target_id}.{request.field}"
    return f"{request.target_id}.{request.field}[{request.index + 1}]"


def _revision_ai_api_error(error: RevisionAIError) -> ApiError:
    status_codes = {
        "REVISION_TARGET_NOT_FOUND": 422,
        "REVISION_FIELD_REQUIRED": 422,
        "REVISION_FIELD_NOT_EDITABLE": 422,
        "REVISION_FIELD_INDEX_UNSUPPORTED": 422,
        "REVISION_FIELD_INDEX_INVALID": 422,
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


def _regenerate_payload(
    db: DBSession,
    project_id: str,
    user: User,
    request: AIRegenerateRequest,
    *,
    on_stage: Callable[[str], None] | None = None,
) -> ArtifactVersionInfo:
    """Shared regeneration used by both the JSON and the streaming response."""
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
        _, target = find_target(snapshot, request.target_type, request.target_id)
        resolve_edit_scope(
            target,
            request.target_type,
            field=request.field,
            index=request.index,
        )
    except RevisionAIError as error:
        raise _revision_ai_api_error(error) from error
    consume_model_quota(user.user_id)
    try:
        # Only forward optional arguments when they are actually used, so existing
        # replacements of `regenerate_target` keep their original signature.
        options: dict[str, Any] = {
            # The picture menu for a whole-slide rewrite, and the allowlist that
            # keeps the model from referencing an upload it was never offered.
            "available_images": list_project_images(db, project.project_id),
        }
        if on_stage is not None:
            options["on_stage"] = on_stage
        if request.field is not None:
            options["field"] = request.field
            options["index"] = request.index
        result = regenerate_target(
            snapshot,
            target_type=request.target_type,
            target_id=request.target_id,
            instruction=request.instruction,
            **options,
        )
    except RevisionAIError as error:
        raise _revision_ai_api_error(error) from error

    if on_stage is not None:
        on_stage("persist")
    version = create_version(
        db,
        project,
        user_id=user.user_id,
        source_plan_id=base.source_plan_id,
        snapshot=result.spec,
        base_version_id=base.artifact_version_id,
        summary=f"AI 局部重生成 {_anchor_label(request)}：{request.instruction.strip()[:160]}",
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


async def _regenerate_event_stream(
    db: DBSession,
    project_id: str,
    user: User,
    request: AIRegenerateRequest,
) -> AsyncIterator[str]:
    """Emit progress frames while the target regenerates, then the new version."""

    def run(emit: Callable[[str, Any], None]) -> None:
        def on_stage(stage: str) -> None:
            emit("progress", frame(REVISION_STAGES, stage, branches=REVISION_BRANCHES))

        payload = _regenerate_payload(db, project_id, user, request, on_stage=on_stage)
        emit("result", payload.model_dump(mode="json"))

    try:
        async for kind, payload in aiter_threaded_producer(run):
            yield encode_sse(kind, payload)
    except ApiError as exc:
        logger.warning("Streamed revision regeneration failed: %s", exc.code)
        yield error_frame(
            exc.message,
            code=exc.code,
            recoverable=exc.recoverable,
            suggested_action=exc.suggested_action,
        )
    except Exception:
        logger.exception("Streamed revision regeneration crashed")
        yield error_frame(
            "局部重生成失败，请重试",
            code="REVISION_STREAM_FAILED",
            suggested_action="请稍后重试；若持续失败，请联系管理员",
        )


@project_router.post(
    "/{project_id}/revisions/regenerate",
    response_model=ArtifactVersionInfo,
    status_code=201,
)
async def regenerate_revision_target(
    http_request: Request,
    project_id: str,
    request: AIRegenerateRequest,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Regenerate one target of an artifact version.

    A client sending `Accept: text/event-stream` gets the model round trips as
    `progress` frames and the new version as the final `result`, which keeps a
    one-to-two minute regeneration observable. Other clients keep JSON.
    """
    if not wants_event_stream(http_request.headers.get("accept")):
        return await asyncio.to_thread(_regenerate_payload, db, project_id, user, request)

    return StreamingResponse(
        _regenerate_event_stream(db, project_id, user, request),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )


@project_router.put(
    "/{project_id}/versions/{version_id}/slides/{slide_id}/image",
    response_model=ArtifactVersionInfo,
    status_code=201,
)
def set_slide_image(
    project_id: str,
    version_id: str,
    slide_id: str,
    request: SlideImageRequest,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Attach, move or remove the picture on one slide as a new version.

    Cleared by sending ``material_id: null``. Like every other revision this
    forks a fresh immutable version, so the previous deck stays reproducible.
    """
    project = get_project_for_user(db, project_id, user)
    base = _version_or_404(db, project, version_id)
    current = get_latest_version(db, project.project_id)
    if current is None or current.artifact_version_id != base.artifact_version_id:
        raise ApiError(
            "成果版本已变化，请基于最新版本重新设置配图",
            code="VERSION_CONFLICT",
            status_code=409,
            details={
                "expected": base.artifact_version_id,
                "current": current.artifact_version_id if current else None,
            },
        )

    snapshot = snapshot_spec(base)
    try:
        slide_index, slide = find_target(snapshot, "slide", slide_id)
    except RevisionAIError as error:
        raise _revision_ai_api_error(error) from error

    data = snapshot.model_dump(mode="json")
    if request.material_id is None:
        data["slides"][slide_index]["image"] = None
        summary = f"移除第 {slide.order} 页配图"
    else:
        material = _project_image(db, project, user, request.material_id)
        data["slides"][slide_index]["image"] = {
            "material_id": material.material_id,
            "placement": request.placement,
            "caption": request.caption.strip(),
        }
        summary = (
            f"第 {slide.order} 页配图设为 {material.original_name}"
            f"（{SLIDE_PLACEMENT_LABELS[request.placement]}）"
        )

    version = create_version(
        db,
        project,
        user_id=user.user_id,
        source_plan_id=base.source_plan_id,
        snapshot=data,
        base_version_id=base.artifact_version_id,
        summary=summary,
        generation_mode="manual",
        expected_latest_version_id=base.artifact_version_id,
    )
    db.commit()
    db.refresh(version)
    return to_version_info(version)


@project_router.post(
    "/{project_id}/versions/{version_id}/illustrate",
    response_model=ArtifactVersionInfo,
    status_code=201,
)
def illustrate_version_slides(
    project_id: str,
    version_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """给这一版里还没有配图的页面各生成一张插图，整批落成一个新版本。

    一页一个版本会把版本历史冲垮，而"这一版补了配图"本来就是同一个动作，所以整批
    只新建一个版本。已有配图的页面保持不动，教师逐页换图仍走配图接口。
    """
    project = get_project_for_user(db, project_id, user)
    base = _version_or_404(db, project, version_id)
    current = get_latest_version(db, project.project_id)
    if current is None or current.artifact_version_id != base.artifact_version_id:
        raise ApiError(
            "只能为最新版本补配图",
            code="VERSION_CONFLICT",
            status_code=409,
            details={
                "expected": base.artifact_version_id,
                "current": current.artifact_version_id if current else None,
            },
            suggested_action="先切回最新版本，再补配图",
        )
    if not image_generation_enabled():
        raise ApiError(
            "尚未配置图像生成服务",
            code="IMAGE_GEN_NOT_CONFIGURED",
            status_code=409,
            suggested_action="配置 ARK_API_KEY 后重试，或手工上传图片再逐页选择",
        )

    data = snapshot_spec(base).model_dump(mode="json")
    attached = illustrate_slides(db, project, data.get("slides") or [], user_id=user.user_id)
    if not attached:
        raise ApiError(
            "这一版没有可自动配图的页面",
            code="NO_SLIDE_NEEDS_IMAGE",
            status_code=409,
            suggested_action="页面都已有配图，或剩余缺图页是卡片/流程/目录等结构版式，请在页面里单独配图",
        )

    version = create_version(
        db,
        project,
        user_id=user.user_id,
        source_plan_id=base.source_plan_id,
        snapshot=data,
        base_version_id=base.artifact_version_id,
        summary=f"为 {len(attached)} 页生成配图",
        generation_mode="manual",
        expected_latest_version_id=base.artifact_version_id,
    )
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
