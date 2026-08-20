"""M5 immutable artifact versions and constrained revision patches."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from backend.core.errors import ApiError
from backend.models.courseware import CoursewarePlan
from backend.models.project import Project
from backend.models.versioning import ArtifactVersion, RevisionPatch
from backend.schemas import (
    ArtifactVersionInfo,
    CoursewarePlanSpec,
    RevisionOperation,
    RevisionPatchInfo,
)

COLLECTIONS = {
    "slide": "slides",
    "lesson_section": "lesson_sections",
    "interaction": "interactions",
}

ALLOWED_FIELDS = {
    "slides": {"title", "purpose", "layout", "bullets", "speaker_notes"},
    "lesson_sections": {
        "title",
        "duration_minutes",
        "objective",
        "teacher_actions",
        "student_actions",
        "assessment",
    },
    "interactions": {"title", "interaction_type", "prompt", "items", "answer_groups"},
}


def get_latest_version(db: DBSession, project_id: str) -> ArtifactVersion | None:
    return (
        db.query(ArtifactVersion)
        .filter(ArtifactVersion.project_id == project_id)
        .order_by(ArtifactVersion.version.desc(), ArtifactVersion.created_at.desc())
        .first()
    )


def get_version(db: DBSession, project_id: str, artifact_version_id: str) -> ArtifactVersion | None:
    return (
        db.query(ArtifactVersion)
        .filter(
            ArtifactVersion.project_id == project_id,
            ArtifactVersion.artifact_version_id == artifact_version_id,
        )
        .first()
    )


def _next_version_number(db: DBSession, project_id: str) -> int:
    return (
        db.query(func.max(ArtifactVersion.version))
        .filter(ArtifactVersion.project_id == project_id)
        .scalar()
        or 0
    ) + 1


def snapshot_spec(version: ArtifactVersion) -> CoursewarePlanSpec:
    try:
        return CoursewarePlanSpec.model_validate(version.snapshot_json or {})
    except Exception as exc:
        raise ApiError(
            "成果版本快照无法读取",
            code="VERSION_SNAPSHOT_INVALID",
            status_code=500,
            recoverable=False,
        ) from exc


def _validated_snapshot(snapshot: CoursewarePlanSpec | dict[str, Any]) -> dict[str, Any]:
    if isinstance(snapshot, CoursewarePlanSpec):
        spec = snapshot
    else:
        try:
            spec = CoursewarePlanSpec.model_validate(snapshot)
        except Exception as exc:
            raise ApiError(
                "修改结果不符合 CoursewarePlan 结构",
                code="REVISION_RESULT_INVALID",
                status_code=422,
                details=str(exc),
            ) from exc
    return spec.model_dump(mode="json")


def create_version(
    db: DBSession,
    project: Project,
    *,
    user_id: str,
    source_plan_id: str,
    snapshot: CoursewarePlanSpec | dict[str, Any],
    base_version_id: str | None,
    summary: str,
) -> ArtifactVersion:
    now = datetime.now(timezone.utc)
    version = ArtifactVersion(
        artifact_version_id=f"av_{__import__('uuid').uuid4().hex[:8]}",
        user_id=user_id,
        project_id=project.project_id,
        source_plan_id=source_plan_id,
        base_version_id=base_version_id,
        version=_next_version_number(db, project.project_id),
        status="ready",
        summary=summary.strip() or "未命名成果版本",
        snapshot_json=_validated_snapshot(snapshot),
        created_at=now,
    )
    db.add(version)
    db.flush()
    project.current_version_id = version.artifact_version_id
    project.updated_at = now
    return version


def ensure_initial_version(
    db: DBSession,
    project: Project,
    plan: CoursewarePlan,
    *,
    user_id: str,
) -> ArtifactVersion:
    existing = (
        db.query(ArtifactVersion)
        .filter(
            ArtifactVersion.project_id == project.project_id,
            ArtifactVersion.source_plan_id == plan.plan_id,
        )
        .order_by(ArtifactVersion.version.desc())
        .first()
    )
    if existing is not None:
        project.current_version_id = existing.artifact_version_id
        return existing

    current = get_latest_version(db, project.project_id)
    return create_version(
        db,
        project,
        user_id=user_id,
        source_plan_id=plan.plan_id,
        snapshot=plan.plan_json or {},
        base_version_id=current.artifact_version_id if current else None,
        summary=f"基于教学蓝图 v{plan.version} 创建成果版本",
    )


def to_version_info(version: ArtifactVersion) -> ArtifactVersionInfo:
    return ArtifactVersionInfo(
        artifact_version_id=version.artifact_version_id,
        user_id=version.user_id,
        project_id=version.project_id,
        source_plan_id=version.source_plan_id,
        base_version_id=version.base_version_id,
        version=version.version,
        status=version.status,
        summary=version.summary,
        snapshot=snapshot_spec(version),
        quality_status=version.quality_status or "pending",
        quality_report=version.quality_report,
        created_at=version.created_at,
    )


def _target(snapshot: dict[str, Any], target_id: str) -> tuple[str, int, dict[str, Any]]:
    for collection_name in ("slides", "lesson_sections", "interactions"):
        items = snapshot.get(collection_name) or []
        for index, item in enumerate(items):
            if item.get("slide_id") == target_id or item.get("section_id") == target_id or item.get("interaction_id") == target_id:
                return collection_name, index, item
    raise ApiError(
        f"Patch 目标不存在：{target_id}",
        code="REVISION_TARGET_NOT_FOUND",
        status_code=422,
        details={"target_id": target_id},
    )


def _target_for_collection(
    snapshot: dict[str, Any], target_id: str, collection_name: str
) -> tuple[int, dict[str, Any]]:
    items = snapshot.get(collection_name) or []
    for index, item in enumerate(items):
        if item.get("slide_id") == target_id or item.get("section_id") == target_id or item.get("interaction_id") == target_id:
            return index, item
    raise ApiError(
        f"Patch 目标不在指定集合中：{target_id}",
        code="REVISION_TARGET_NOT_FOUND",
        status_code=422,
        details={"target_id": target_id, "collection": collection_name},
    )


def _require_target(operation: RevisionOperation) -> str:
    if not operation.target_id:
        raise ApiError(
            "Patch 操作缺少 target_id",
            code="REVISION_TARGET_REQUIRED",
            status_code=422,
        )
    return operation.target_id


def _regenerate(item: dict[str, Any], instruction: str) -> None:
    lower = instruction.lower()
    if any(word in instruction for word in ("简化", "精简", "缩短")):
        if "bullets" in item:
            item["bullets"] = [str(value)[:80] for value in (item.get("bullets") or [])[:3]]
        if item.get("speaker_notes"):
            item["speaker_notes"] = str(item["speaker_notes"]).split("。", 1)[0].strip("。")
        return

    if "案例" in instruction and any(word in instruction for word in ("增加", "补充", "添加")):
        case = re.split(r"案例[：:，, ]*", instruction, maxsplit=1)[-1].strip("。 ")
        case = case if case and case != instruction else "结合本课主题设计一个生活案例"
        if "bullets" in item:
            value = f"案例：{case}"
            if value not in item["bullets"]:
                item["bullets"].append(value)
        elif "prompt" in item:
            item["prompt"] = f"{item.get('prompt', '').rstrip('。')}；补充案例：{case}"
        return

    if any(word in instruction for word in ("扩写", "展开", "补充说明")):
        if "bullets" in item:
            item["bullets"].append(f"延伸提示：{instruction.strip()}")
        elif "objective" in item:
            item["objective"] = f"{item.get('objective', '')}；{instruction.strip()}"
        return

    raise ApiError(
        "当前本地修改解释器无法安全映射该指令",
        code="REVISION_INTERPRETATION_UNSUPPORTED",
        status_code=422,
        details={"instruction": instruction, "normalized": lower},
        suggested_action="请明确指定页面和修改字段，或提交结构化 Patch",
    )


def _normalize_orders(snapshot: dict[str, Any]) -> None:
    for collection_name in ("slides", "lesson_sections", "interactions"):
        for index, item in enumerate(snapshot.get(collection_name) or [], start=1):
            item["order"] = index


def apply_operations(
    snapshot: CoursewarePlanSpec | dict[str, Any], operations: list[RevisionOperation]
) -> CoursewarePlanSpec:
    data = _validated_snapshot(snapshot)
    for operation in operations:
        op = operation.op
        target_id = operation.target_id

        if op == "insert":
            if operation.target_type is None:
                raise ApiError(
                    "insert 操作必须指定 target_type",
                    code="REVISION_TARGET_TYPE_REQUIRED",
                    status_code=422,
                )
            if not isinstance(operation.value, dict):
                raise ApiError(
                    "insert 操作的 value 必须是对象",
                    code="REVISION_VALUE_INVALID",
                    status_code=422,
                )
            collection_name = COLLECTIONS[operation.target_type]
            items = data.setdefault(collection_name, [])
            identifier = {
                "slide": "slide_id",
                "lesson_section": "section_id",
                "interaction": "interaction_id",
            }[operation.target_type]
            candidate = deepcopy(operation.value)
            if not candidate.get(identifier):
                raise ApiError(
                    "insert 操作必须提供稳定 ID",
                    code="REVISION_STABLE_ID_REQUIRED",
                    status_code=422,
                )
            if any(item.get(identifier) == candidate[identifier] for item in items):
                raise ApiError(
                    f"稳定 ID 已存在：{candidate[identifier]}",
                    code="REVISION_DUPLICATE_ID",
                    status_code=422,
                )
            if operation.after_id:
                after_index, _ = _target_for_collection(data, operation.after_id, collection_name)
                items.insert(after_index + 1, candidate)
            else:
                items.append(candidate)
            continue

        if not target_id:
            raise ApiError(
                f"{op} 操作必须指定 target_id",
                code="REVISION_TARGET_REQUIRED",
                status_code=422,
            )
        collection_name, index, item = _target(data, target_id)

        if op == "replace":
            field = operation.field
            if field not in ALLOWED_FIELDS[collection_name]:
                raise ApiError(
                    f"不允许修改字段：{field or '未指定'}",
                    code="REVISION_FIELD_NOT_ALLOWED",
                    status_code=422,
                    details={"allowed": sorted(ALLOWED_FIELDS[collection_name])},
                )
            if operation.value is None:
                raise ApiError(
                    "replace 操作必须提供 value",
                    code="REVISION_VALUE_REQUIRED",
                    status_code=422,
                )
            item[field] = deepcopy(operation.value)
        elif op == "delete":
            if collection_name == "slides" and len(data.get(collection_name) or []) <= 1:
                raise ApiError(
                    "课程至少需要保留一页幻灯片",
                    code="REVISION_LAST_SLIDE",
                    status_code=422,
                )
            data[collection_name].pop(index)
        elif op == "move":
            if operation.after_id:
                after_collection, after_index, _ = _target(data, operation.after_id)
                if after_collection != collection_name:
                    raise ApiError(
                        "页面只能在同一页面集合内移动",
                        code="REVISION_MOVE_COLLECTION_MISMATCH",
                        status_code=422,
                    )
                moved = data[collection_name].pop(index)
                if after_index > index:
                    after_index -= 1
                data[collection_name].insert(after_index + 1, moved)
            else:
                raise ApiError(
                    "move 操作必须指定 after_id",
                    code="REVISION_MOVE_TARGET_REQUIRED",
                    status_code=422,
                )
        elif op == "regenerate":
            _regenerate(item, operation.instruction or "")

    _normalize_orders(data)
    try:
        return CoursewarePlanSpec.model_validate(data)
    except Exception as exc:
        raise ApiError(
            "Patch 应用结果未通过 CoursewarePlan 校验",
            code="REVISION_RESULT_INVALID",
            status_code=422,
            details=str(exc),
        ) from exc


def interpret_instruction(
    snapshot: CoursewarePlanSpec,
    instruction: str,
) -> tuple[str, list[str], list[RevisionOperation], list[str], bool, str]:
    """Map a small, auditable subset of Chinese edit requests to operations."""
    text = instruction.strip()
    slides = snapshot.slides
    page_match = re.search(r"(?:第\s*(\d+)\s*页|slide[_ -]?(\d+))", text, re.IGNORECASE)
    target_id = None
    if page_match:
        order = int(page_match.group(1) or page_match.group(2))
        target = next((slide for slide in slides if slide.order == order), None)
        if target is None:
            raise ApiError(
                f"找不到第 {order} 页",
                code="REVISION_TARGET_NOT_FOUND",
                status_code=422,
            )
        target_id = target.slide_id

    move_match = re.search(
        r"第\s*(\d+)\s*页.*(?:移动|移到|放到).*第\s*(\d+)\s*页(?:之后|后)", text
    )
    if move_match:
        source_order, after_order = (int(move_match.group(1)), int(move_match.group(2)))
        source = next((slide for slide in slides if slide.order == source_order), None)
        after = next((slide for slide in slides if slide.order == after_order), None)
        if source is None or after is None:
            raise ApiError("移动目标页面不存在", code="REVISION_TARGET_NOT_FOUND", status_code=422)
        operation = RevisionOperation(op="move", target_id=source.slide_id, after_id=after.slide_id)
        return "slide", [source.slide_id, after.slide_id], [operation], [], False, "调整页面顺序"

    if not target_id:
        raise ApiError(
            "无法从修改意见确定目标页面",
            code="REVISION_TARGET_UNCLEAR",
            status_code=422,
            suggested_action="请使用“第 N 页”或明确的稳定 ID 指定修改范围",
        )

    title_match = re.search(r"标题.*?(?:改为|修改为|换成|设为)\s*[：: ]?(.+)$", text)
    if title_match:
        value = title_match.group(1).strip(" 。.!！")
        operation = RevisionOperation(op="replace", target_id=target_id, field="title", value=value)
        return "slide", [target_id], [operation], ["lesson_plan_activity_ref"], False, "修改页面标题"

    if any(word in text for word in ("简化", "精简", "缩短", "扩写", "展开", "案例", "补充说明")):
        operation = RevisionOperation(op="regenerate", target_id=target_id, instruction=text)
        return "slide", [target_id], [operation], ["lesson_plan_activity_ref"], False, "局部调整页面内容"

    if any(word in text for word in ("删除", "移除")):
        operation = RevisionOperation(op="delete", target_id=target_id)
        return "slide", [target_id], [operation], ["lesson_plan_activity_ref"], True, "删除指定页面"

    raise ApiError(
        "当前本地修改解释器无法安全映射该指令",
        code="REVISION_INTERPRETATION_UNSUPPORTED",
        status_code=422,
        suggested_action="请明确指定标题修改、简化、扩写、案例、删除或移动页面",
    )


def create_patch(
    db: DBSession,
    *,
    user_id: str,
    project_id: str,
    base_version_id: str,
    instruction: str,
    scope: str,
    target_ids: list[str],
    operations: list[RevisionOperation],
    cascade_check: list[str],
    requires_confirmation: bool,
    summary: str,
) -> RevisionPatch:
    patch = RevisionPatch(
        patch_id=f"patch_{__import__('uuid').uuid4().hex[:8]}",
        user_id=user_id,
        project_id=project_id,
        base_version_id=base_version_id,
        instruction=instruction,
        scope=scope,
        target_ids=target_ids,
        operations_json=[operation.model_dump(mode="json") for operation in operations],
        cascade_check=cascade_check,
        requires_confirmation=requires_confirmation,
        status="preview",
        summary=summary,
        created_at=datetime.now(timezone.utc),
    )
    db.add(patch)
    db.flush()
    return patch


def to_patch_info(patch: RevisionPatch) -> RevisionPatchInfo:
    return RevisionPatchInfo(
        patch_id=patch.patch_id,
        user_id=patch.user_id,
        project_id=patch.project_id,
        base_version_id=patch.base_version_id,
        created_version_id=patch.created_version_id,
        instruction=patch.instruction,
        scope=patch.scope,
        target_ids=patch.target_ids or [],
        operations=[RevisionOperation.model_validate(item) for item in (patch.operations_json or [])],
        cascade_check=patch.cascade_check or [],
        requires_confirmation=patch.requires_confirmation,
        status=patch.status,
        summary=patch.summary,
        created_at=patch.created_at,
        applied_at=patch.applied_at,
    )


def apply_patch(
    db: DBSession,
    project: Project,
    patch: RevisionPatch,
    *,
    user_id: str,
) -> ArtifactVersion:
    if patch.status != "preview":
        raise ApiError(
            "该 Patch 已经处理，不能重复应用",
            code="REVISION_PATCH_NOT_PENDING",
            status_code=409,
        )
    current = get_latest_version(db, project.project_id)
    if current is None or current.artifact_version_id != patch.base_version_id:
        raise ApiError(
            "成果版本已变化，请基于最新版本重新生成修改意见",
            code="VERSION_CONFLICT",
            status_code=409,
            details={"expected": patch.base_version_id, "current": current.artifact_version_id if current else None},
        )
    operations = [RevisionOperation.model_validate(item) for item in (patch.operations_json or [])]
    result = apply_operations(snapshot_spec(current), operations)
    version = create_version(
        db,
        project,
        user_id=user_id,
        source_plan_id=current.source_plan_id,
        snapshot=result,
        base_version_id=current.artifact_version_id,
        summary=patch.summary,
    )
    patch.created_version_id = version.artifact_version_id
    patch.status = "applied"
    patch.applied_at = datetime.now(timezone.utc)
    return version


def restore_version(
    db: DBSession,
    project: Project,
    target: ArtifactVersion,
    *,
    user_id: str,
    summary: str | None = None,
) -> ArtifactVersion:
    current = get_latest_version(db, project.project_id)
    return create_version(
        db,
        project,
        user_id=user_id,
        source_plan_id=target.source_plan_id,
        snapshot=snapshot_spec(target),
        base_version_id=current.artifact_version_id if current else None,
        summary=summary or f"从成果版本 v{target.version} 恢复",
    )
