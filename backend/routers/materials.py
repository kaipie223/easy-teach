"""Project-scoped material upload, parsing and evidence APIs."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DBSession

from backend.config import settings
from backend.core.errors import ApiError
from backend.core.ownership import get_material_for_user, get_project_for_user, get_session_for_user
from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.models.material import EvidenceChunk, Material, MaterialAnalysis, MaterialBinding
from backend.models.user import User
from backend.schemas import (
    EvidenceInfo,
    MaterialAnalysisInfo,
    MaterialBindingInfo,
    MaterialBindingReplaceRequest,
    MaterialInfo,
    SlideImageGenerateRequest,
    StageInfo,
)
from backend.services.task_queue import enqueue_material_analysis
from backend.services.image_generation import (
    ImageGenerationError,
    generate_image,
    image_generation_enabled,
)
from backend.services.materials import (
    MaterialValidationError,
    ParsedChunk,
    ParsedMaterial,
    apply_parsed_material,
    detect_file_type_from_path,
    fail_material_analysis,
    parse_material,
    parser_identity,
)
from backend.services.progress import label_of, manifest, material_stages, percent_of
from backend.services.slide_illustration import store_generated_image
from backend.services.limits import (
    consume_model_quota,
    ensure_storage_capacity,
    remaining_storage_bytes,
)
from backend.services.uploads import (
    UploadSizeExceeded,
    checksum_file,
    remove_managed_file,
    remove_staged_upload,
    stream_upload_to_path,
)

router = APIRouter()


def _material_info(material: Material) -> MaterialInfo:
    """Fill the parsing progress that lives on the material row.

    步骤清单取决于资料类型（图片多一步视觉识别，视频多一步转录解析），所以整张
    清单随资料一起下发，前端不必知道哪个类型该走哪些步骤。
    """
    info = MaterialInfo.model_validate(material)
    stages = material_stages(material.file_type)
    info.stages = [StageInfo(**item) for item in manifest(stages)]
    info.stage_label = label_of(stages, material.stage)
    # 已完成的资料就是 100%，不该把"最后一步 92%"留在列表里
    info.stage_percent = (
        100 if material.status == "ready" else percent_of(stages, material.stage)
    )
    return info


def _analysis_info(analysis: MaterialAnalysis) -> MaterialAnalysisInfo:
    return MaterialAnalysisInfo.model_validate(analysis)


def _binding_info(binding: MaterialBinding) -> MaterialBindingInfo:
    return MaterialBindingInfo.model_validate(binding)


def _evidence_info(evidence: EvidenceChunk) -> EvidenceInfo:
    return EvidenceInfo.model_validate(evidence)


@router.get("/projects/{project_id}/materials", response_model=list[MaterialInfo])
def list_materials(
    project_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    materials = (
        db.query(Material)
        .filter(Material.project_id == project.project_id, Material.deleted_at.is_(None))
        .order_by(Material.created_at.desc())
        .all()
    )
    return [_material_info(material) for material in materials]


@router.post("/projects/{project_id}/materials", response_model=MaterialInfo, status_code=201)
async def upload_material(
    project_id: str,
    file: UploadFile = File(...),
    session_id: str | None = Form(default=None),
    ref_description: str = Form(default=""),
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, user)
    if not file.filename:
        raise ApiError("文件名为空", code="INVALID_MATERIAL_NAME", status_code=400)

    session = None
    if session_id:
        session = get_session_for_user(db, session_id, user)
        if session.project_id != project.project_id:
            raise ApiError("会话不属于当前项目", code="SESSION_PROJECT_MISMATCH", status_code=409)

    material_id = f"mat_{uuid.uuid4().hex[:24]}"
    safe_name = Path(file.filename).name
    stored_path = settings.upload_dir / material_id / safe_name
    max_file_bytes = settings.max_upload_size_mb * 1024 * 1024
    remaining_bytes = remaining_storage_bytes(db, user.user_id)
    if remaining_bytes <= 0:
        raise ApiError(
            "个人存储空间不足",
            code="STORAGE_QUOTA_EXCEEDED",
            status_code=413,
            suggested_action="请删除不再需要的资料后重试",
        )

    try:
        stored = await stream_upload_to_path(
            file,
            stored_path,
            max_bytes=min(max_file_bytes, remaining_bytes),
        )
    except UploadSizeExceeded as exc:
        quota_limited = remaining_bytes < max_file_bytes
        raise ApiError(
            "个人存储空间不足" if quota_limited else f"文件超过 {settings.max_upload_size_mb} MB 限制",
            code="STORAGE_QUOTA_EXCEEDED" if quota_limited else "MATERIAL_TOO_LARGE",
            status_code=413,
            details={"max_bytes": exc.max_bytes, "actual_bytes": exc.actual_bytes},
        ) from exc

    try:
        file_type, mime_type = detect_file_type_from_path(safe_name, stored_path)
    except MaterialValidationError as exc:
        remove_staged_upload(stored_path)
        raise ApiError(str(exc), code=exc.code, status_code=422, details=exc.details) from exc

    if file_type == "video" and not settings.video_parser_enabled:
        remove_staged_upload(stored_path)
        raise ApiError(
            "视频资料解析暂未启用",
            code="VIDEO_CAPABILITY_DISABLED",
            status_code=409,
            suggested_action="请先上传 PDF、Word 或 PPT 资料",
        )

    if file_type == "image" and stored.size_bytes > 15 * 1024 * 1024:
        remove_staged_upload(stored_path)
        raise ApiError(
            "图片超过 15 MB 限制",
            code="IMAGE_TOO_LARGE",
            status_code=413,
        )

    now = datetime.now(timezone.utc)
    material = Material(
        material_id=material_id,
        owner_id=user.user_id,
        project_id=project.project_id,
        session_id=session.session_id if session else None,
        original_name=safe_name,
        file_type=file_type,
        mime_type=mime_type,
        stored_path=str(stored_path),
        size_bytes=stored.size_bytes,
        checksum_sha256=stored.checksum_sha256,
        status="queued" if file_type == "video" else "processing",
        ref_description=ref_description.strip() or None,
        created_at=now,
        updated_at=now,
    )
    parser_name, parser_version = parser_identity(file_type)
    analysis = MaterialAnalysis(
        analysis_id=f"analysis_{uuid.uuid4().hex[:24]}",
        material_id=material_id,
        run_number=1,
        parser_name=parser_name,
        parser_version=parser_version,
        status="pending" if file_type == "video" else "processing",
        started_at=None if file_type == "video" else now,
        created_at=now,
        updated_at=now,
    )
    db.add_all([material, analysis])
    db.commit()

    if file_type == "video":
        try:
            enqueue_material_analysis(analysis.analysis_id, force=True, db=db)
        except Exception as exc:
            db.rollback()
            material = db.query(Material).filter(Material.material_id == material_id).one()
            analysis = db.query(MaterialAnalysis).filter(MaterialAnalysis.analysis_id == analysis.analysis_id).one()
            fail_material_analysis(db, material, analysis, exc)
            db.commit()
            raise
        db.refresh(material)
        return _material_info(material)

    try:
        parsed = await run_in_threadpool(parse_material, file_type, stored_path)
        apply_parsed_material(db, material, analysis, parsed)
        db.commit()
    except Exception as exc:
        db.rollback()
        material = db.query(Material).filter(Material.material_id == material_id).one()
        analysis = db.query(MaterialAnalysis).filter(MaterialAnalysis.analysis_id == analysis.analysis_id).one()
        fail_material_analysis(db, material, analysis, exc)
        db.commit()
        raise ApiError(
            "资料解析失败",
            code="MATERIAL_PARSE_FAILED",
            status_code=422,
            details={"material_id": material_id},
        ) from exc

    return _material_info(material)


@router.post(
    "/projects/{project_id}/images/generate",
    response_model=MaterialInfo,
    status_code=201,
)
def generate_project_image(
    project_id: str,
    request: SlideImageGenerateRequest,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """用 AI 生成一张配图，并直接登记成该项目的图片资料。

    生成结果与手工上传走完全相同的资料结构，所以随后用现有的
    "应用配图并创建版本" 接口绑定到页面即可：绑定、导出渲染、配额校验
    都不需要第二套逻辑。这样成果编辑里就不再强制"先上传图片再选图"。
    """
    project = get_project_for_user(db, project_id, user)
    if not image_generation_enabled():
        raise ApiError(
            "尚未配置图像生成服务",
            code="IMAGE_GEN_NOT_CONFIGURED",
            status_code=409,
            suggested_action="在 .env 配置 ARK_API_KEY 后重试，或改用手工上传图片",
        )

    consume_model_quota(user.user_id)
    try:
        generated = generate_image(request.prompt)
    except ImageGenerationError as exc:
        raise ApiError(
            str(exc),
            code=exc.code,
            status_code=409 if exc.code in {"IMAGE_PROMPT_REQUIRED"} else 502,
            suggested_action="调整提示词后重试，或改用手工上传图片",
        ) from exc

    material = store_generated_image(
        db,
        project,
        user_id=user.user_id,
        generated=generated,
        prompt=request.prompt,
    )
    return _material_info(material)


@router.get("/materials/{material_id}", response_model=MaterialInfo)
def get_material(
    material_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _material_info(get_material_for_user(db, material_id, user))


@router.get("/materials/{material_id}/download")
def download_material(
    material_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    material = get_material_for_user(db, material_id, user)
    path = Path(material.stored_path)
    if not path.is_file():
        raise ApiError(
            "资料文件不存在",
            code="MATERIAL_FILE_NOT_FOUND",
            status_code=404,
            suggested_action="请重新上传该资料",
        )
    return FileResponse(
        path,
        media_type=material.mime_type or "application/octet-stream",
        filename=material.original_name,
    )


@router.delete("/materials/{material_id}", response_model=MaterialInfo)
def delete_material(
    material_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    material = get_material_for_user(db, material_id, user)
    try:
        remove_managed_file(Path(material.stored_path), root=settings.upload_dir)
    except ValueError as exc:
        raise ApiError(
            "资料存储路径异常，拒绝删除",
            code="MATERIAL_STORAGE_PATH_INVALID",
            status_code=500,
        ) from exc
    now = datetime.now(timezone.utc)
    material.deleted_at = now
    material.status = "archived"
    material.size_bytes = 0
    material.updated_at = now
    (
        db.query(MaterialBinding)
        .filter(
            MaterialBinding.material_id == material.material_id,
            MaterialBinding.is_active.is_(True),
        )
        .update(
            {"is_active": False, "invalidated_at": now, "updated_at": now},
            synchronize_session=False,
        )
    )
    (
        db.query(EvidenceChunk)
        .filter(
            EvidenceChunk.material_id == material.material_id,
            EvidenceChunk.is_valid.is_(True),
        )
        .update(
            {
                "is_valid": False,
                "invalidated_at": now,
                "invalidation_reason": "material_deleted",
            },
            synchronize_session=False,
        )
    )
    db.commit()
    db.refresh(material)
    return _material_info(material)


@router.get("/materials/{material_id}/analysis", response_model=MaterialAnalysisInfo)
def get_material_analysis(
    material_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    material = get_material_for_user(db, material_id, user)
    analysis = (
        db.query(MaterialAnalysis)
        .filter(MaterialAnalysis.material_id == material.material_id)
        .order_by(MaterialAnalysis.run_number.desc())
        .first()
    )
    if analysis is None:
        raise ApiError("资料解析记录不存在", code="MATERIAL_ANALYSIS_NOT_FOUND", status_code=404)
    return _analysis_info(analysis)


@router.get("/materials/{material_id}/evidence", response_model=list[EvidenceInfo])
def list_material_evidence(
    material_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    material = get_material_for_user(db, material_id, user)
    evidence = (
        db.query(EvidenceChunk)
        .filter(EvidenceChunk.material_id == material.material_id, EvidenceChunk.is_valid.is_(True))
        .order_by(EvidenceChunk.chunk_index.asc())
        .all()
    )
    return [_evidence_info(item) for item in evidence]


@router.put("/materials/{material_id}/bindings", response_model=list[MaterialBindingInfo])
def replace_material_bindings(
    material_id: str,
    request: MaterialBindingReplaceRequest,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    material = get_material_for_user(db, material_id, user)
    if material.project_id is None:
        raise ApiError("资料尚未关联项目", code="MATERIAL_PROJECT_REQUIRED", status_code=409)
    project = get_project_for_user(db, material.project_id, user)
    now = datetime.now(timezone.utc)
    (
        db.query(MaterialBinding)
        .filter(
            MaterialBinding.material_id == material.material_id,
            MaterialBinding.project_id == project.project_id,
            MaterialBinding.is_active.is_(True),
        )
        .update({"is_active": False, "invalidated_at": now, "updated_at": now}, synchronize_session=False)
    )
    bindings = []
    for item in request.bindings:
        binding = MaterialBinding(
            binding_id=f"bind_{uuid.uuid4().hex[:24]}",
            material_id=material.material_id,
            project_id=project.project_id,
            created_by=user.user_id,
            usage_type=item.usage_type.value,
            target_type=item.target_type.value,
            target_id=item.target_id,
            teacher_instruction=item.teacher_instruction,
            suggested_by_ai=item.suggested_by_ai,
            confirmed_by_teacher=item.confirmed_by_teacher,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(binding)
        bindings.append(binding)
    db.commit()
    for binding in bindings:
        db.refresh(binding)
    return [_binding_info(binding) for binding in bindings]


@router.get("/materials/{material_id}/bindings", response_model=list[MaterialBindingInfo])
def list_material_bindings(
    material_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    material = get_material_for_user(db, material_id, user)
    bindings = (
        db.query(MaterialBinding)
        .filter(MaterialBinding.material_id == material.material_id, MaterialBinding.is_active.is_(True))
        .order_by(MaterialBinding.created_at.asc())
        .all()
    )
    return [_binding_info(binding) for binding in bindings]
