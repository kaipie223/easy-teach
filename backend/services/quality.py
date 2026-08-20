"""Deterministic quality checks for generated courseware snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.schemas import CoursewarePlanSpec


def _issue(code: str, message: str, *, path: str | None = None) -> dict[str, str]:
    item = {"code": code, "message": message}
    if path:
        item["path"] = path
    return item


def inspect_courseware(snapshot: CoursewarePlanSpec | dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe report without changing the source snapshot.

    Schema validation catches malformed values. These checks cover the
    cross-item invariants that are easy to break during local revisions.
    """
    try:
        spec = (
            snapshot
            if isinstance(snapshot, CoursewarePlanSpec)
            else CoursewarePlanSpec.model_validate(snapshot)
        )
    except Exception as exc:
        return {
            "status": "failed",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "errors": [_issue("SCHEMA_INVALID", "成果快照未通过 CoursewarePlan 校验")],
            "warnings": [],
            "metrics": {},
            "details": str(exc),
        }

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    collections = {
        "slides": spec.slides,
        "lesson_sections": spec.lesson_sections,
        "interactions": spec.interactions,
    }
    if not spec.slides:
        errors.append(_issue("NO_SLIDES", "成果至少需要包含一页幻灯片", path="slides"))

    for name, items in collections.items():
        ids = [
            item.slide_id
            if name == "slides"
            else item.section_id
            if name == "lesson_sections"
            else item.interaction_id
            for item in items
        ]
        if len(ids) != len(set(ids)):
            errors.append(_issue("DUPLICATE_STABLE_ID", f"{name} 包含重复的稳定 ID", path=name))

        if name != "interactions":
            orders = [item.order for item in items]
            if orders != list(range(1, len(items) + 1)):
                errors.append(
                    _issue("ORDER_NOT_CONTIGUOUS", f"{name} 的 order 必须从 1 连续递增", path=name)
                )

    for index, slide in enumerate(spec.slides):
        if not slide.title.strip():
            errors.append(_issue("EMPTY_TITLE", "幻灯片标题不能为空", path=f"slides[{index}].title"))
        if not slide.bullets:
            warnings.append(_issue("EMPTY_BULLETS", "幻灯片没有要点内容", path=f"slides[{index}].bullets"))

    for index, section in enumerate(spec.lesson_sections):
        if not section.title.strip() or not section.objective.strip():
            errors.append(
                _issue(
                    "INCOMPLETE_LESSON_SECTION",
                    "教案章节必须包含标题和教学目标",
                    path=f"lesson_sections[{index}]",
                )
            )

    for index, interaction in enumerate(spec.interactions):
        if not interaction.prompt.strip():
            errors.append(
                _issue("EMPTY_INTERACTION_PROMPT", "互动题目必须包含提示语", path=f"interactions[{index}].prompt")
            )
        if not interaction.items:
            warnings.append(
                _issue("EMPTY_INTERACTION_ITEMS", "互动内容没有可操作的项目", path=f"interactions[{index}].items")
            )

    section_minutes = sum(section.duration_minutes for section in spec.lesson_sections)
    if section_minutes and abs(section_minutes - spec.duration_minutes) > max(5, round(spec.duration_minutes * 0.2)):
        warnings.append(
            _issue(
                "DURATION_MISMATCH",
                f"教案章节总时长 {section_minutes} 分钟与课程时长 {spec.duration_minutes} 分钟差异较大",
                path="lesson_sections",
            )
        )
    if not spec.evidence_refs:
        warnings.append(_issue("NO_EVIDENCE_REFS", "成果没有关联可回溯的证据来源", path="evidence_refs"))

    status = "failed" if errors else "warning" if warnings else "passed"
    return {
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "slide_count": len(spec.slides),
            "lesson_section_count": len(spec.lesson_sections),
            "interaction_count": len(spec.interactions),
            "evidence_ref_count": len(spec.evidence_refs),
            "lesson_duration_minutes": section_minutes,
        },
    }


def require_courseware_quality(snapshot: CoursewarePlanSpec | dict[str, Any]) -> dict[str, Any]:
    """Raise for blocking quality errors and return the report otherwise."""
    report = inspect_courseware(snapshot)
    if report["status"] == "failed":
        messages = "; ".join(item["message"] for item in report["errors"])
        raise RuntimeError(f"成果质量检查失败：{messages}")
    return report
