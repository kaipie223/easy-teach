from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "0.1"
PACKAGE_MANIFEST_VERSION = "0.1"
PLAN_VERSION = "0.1"
BUILDER_VERSION = "0.1.0"
GENERATOR_VERSION = "0.1.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_relative_path(value: str) -> str:
    if not value:
        raise ValueError("path must not be empty")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if normalized.startswith("/") or ":" in normalized.split("/")[0] or ".." in path.parts:
        raise ValueError("path must be a package-relative POSIX path")
    return normalized


class IRBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class PackageTimeRange(IRBaseModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_order(self) -> "PackageTimeRange":
        if self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds must be greater than or equal to start_seconds")
        return self


class EvidenceRef(IRBaseModel):
    evidence_id: str
    evidence_type: str
    source_id: str
    time_range: PackageTimeRange | None = None
    excerpt: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: Literal["observed", "inferred", "corrected", "unresolved"] = "observed"


class AssetRef(IRBaseModel):
    asset_id: str
    kind: Literal["keyframe", "crop", "formula", "chart", "diagram", "audio", "other"]
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    mime_type: str = "application/octet-stream"
    required: bool = True
    source_id: str | None = None
    time_range: PackageTimeRange | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    _path_is_relative = field_validator("path")(_validate_relative_path)


class ContentBlock(IRBaseModel):
    id: str
    block_type: Literal[
        "text",
        "formula",
        "table",
        "chart",
        "diagram",
        "code",
        "question",
        "options",
        "solution",
        "answer",
        "operation_step",
        "image",
        "unknown_visual",
    ]
    text: str = ""
    latex: str | None = None
    asset_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: Literal["observed", "inferred", "corrected", "unresolved"] = "observed"
    visibility: Literal["teacher", "student", "hidden"] = "student"
    review_flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TeachingUnit(IRBaseModel):
    id: str
    time_range: PackageTimeRange
    page_range: list[int] = Field(default_factory=list)
    topic: str
    topic_path: list[str] = Field(default_factory=list)
    pedagogical_roles: list[str] = Field(default_factory=list)
    content_blocks: list[ContentBlock] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    relation_refs: list[str] = Field(default_factory=list)
    source_segment_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    status: Literal["observed", "inferred", "corrected", "unresolved"] = "observed"
    review_flags: list[str] = Field(default_factory=list)
    boundary_score: float = Field(default=0.5, ge=0, le=1)


class Relation(IRBaseModel):
    id: str
    relation_type: Literal[
        "refers_to",
        "derives_from",
        "explains",
        "answers",
        "contrasts",
        "prerequisite_of",
    ]
    from_id: str
    to_id: str
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


class UnresolvedItem(IRBaseModel):
    id: str
    item_type: str
    description: str
    candidate_values: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    review_required: bool = True
    status: Literal["unresolved", "corrected"] = "unresolved"


class ConflictItem(IRBaseModel):
    id: str
    conflict_type: Literal[
        "ASR_OCR",
        "ASR_VISUAL",
        "OCR_VISUAL",
        "boundary_mismatch",
        "semantic_mismatch",
        "chapter_overlap",
        "insufficient_evidence",
        "schema_or_range_error",
        "other",
    ]
    description: str
    candidate_values: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    resolution: str | None = None
    review_required: bool = True
    severity: Literal["low", "medium", "high"] = "medium"
    status: Literal["open", "resolved", "accepted", "rejected"] = "open"
    recommended_action: str = "review"


class QualitySummary(IRBaseModel):
    status: Literal["ok", "warning", "error"] = "ok"
    evidence_coverage: float = Field(default=0, ge=0, le=1)
    unresolved_count: int = Field(default=0, ge=0)
    conflict_count: int = Field(default=0, ge=0)
    unit_count: int = Field(default=0, ge=0)
    block_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)


class TeachingContentIR(IRBaseModel):
    schema_version: str = SCHEMA_VERSION
    package_id: str
    package_version: str = "0.1.0"
    created_at: datetime = Field(default_factory=utc_now)
    course_context: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list)
    teaching_units: list[TeachingUnit] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    assets: list[AssetRef] = Field(default_factory=list)
    unresolved_items: list[UnresolvedItem] = Field(default_factory=list)
    conflicts: list[ConflictItem] = Field(default_factory=list)
    quality: QualitySummary = Field(default_factory=QualitySummary)


class PackageFile(IRBaseModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    required: bool = True
    role: Literal["manifest", "source", "ir", "asset", "quality", "artifact"]

    _path_is_relative = field_validator("path")(_validate_relative_path)


class TeachingContentManifest(IRBaseModel):
    manifest_version: str = PACKAGE_MANIFEST_VERSION
    package_id: str
    package_version: str
    created_at: datetime = Field(default_factory=utc_now)
    builder_version: str = BUILDER_VERSION
    source_video: dict[str, Any] = Field(default_factory=dict)
    schema_versions: dict[str, str] = Field(default_factory=dict)
    source_result_path: str = "source/video_parse_result.json"
    ir_path: str = "ir/teaching_content_ir.json"
    quality_report_path: str = "quality/parse_quality_report.json"
    files: list[PackageFile] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)
    status: Literal["complete", "partial", "invalid"] = "complete"
    warnings: list[str] = Field(default_factory=list)

    _source_path_is_relative = field_validator("source_result_path", "ir_path", "quality_report_path")(_validate_relative_path)


class DemoGenerationRequest(IRBaseModel):
    schema_version: str = "0.1"
    outputs: list[Literal["pptx", "docx", "html"]] = Field(default_factory=lambda: ["pptx", "docx", "html"])
    audience: str = "初中学生"
    style: str = "清晰、简洁、可复习"
    title: str | None = None
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    include_answer_key: bool = True
    student_mode: bool = True
    max_slides: int = Field(default=12, ge=3, le=60)
    estimated_minutes: int = Field(default=20, ge=5, le=180)


class PlanStage(IRBaseModel):
    stage_id: str
    unit_id: str
    title: str
    purpose: str
    content: list[str] = Field(default_factory=list)
    pedagogical_role: str = "explanation"
    evidence_refs: list[str] = Field(default_factory=list)
    asset_refs: list[str] = Field(default_factory=list)
    status: Literal["observed", "inferred", "suggested"] = "observed"
    student_visible: bool = True


class DemoGenerationPlan(IRBaseModel):
    schema_version: str = PLAN_VERSION
    plan_id: str
    package_id: str
    package_version: str
    ir_schema_version: str
    created_at: datetime = Field(default_factory=utc_now)
    request: DemoGenerationRequest
    title: str
    course_context: dict[str, Any] = Field(default_factory=dict)
    learning_objectives: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    stages: list[PlanStage] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    asset_refs: list[str] = Field(default_factory=list)
    estimated_minutes: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)
    generator_version: str = GENERATOR_VERSION


class SpecElement(IRBaseModel):
    id: str
    element_type: Literal["text", "bullet", "formula", "table", "chart", "image", "question", "answer", "source"]
    text: str = ""
    latex: str | None = None
    asset_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    visibility: Literal["student", "teacher", "hidden"] = "student"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SlideSpec(IRBaseModel):
    schema_version: str = "0.1"
    slide_id: str
    order: int = Field(ge=1)
    role: Literal["title", "objective", "explanation", "example", "question", "summary", "source"]
    title: str
    elements: list[SpecElement] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    asset_refs: list[str] = Field(default_factory=list)
    answer_reveal: bool = False
    constraints: dict[str, Any] = Field(default_factory=lambda: {"max_chars": 480, "min_font_pt": 18, "max_images": 3})


class SlideDeckSpec(IRBaseModel):
    schema_version: str = "0.1"
    plan_id: str
    slides: list[SlideSpec] = Field(default_factory=list)


class LessonPlanSection(IRBaseModel):
    section_id: str
    title: str
    minutes: int = Field(ge=0)
    teacher_activity: list[str] = Field(default_factory=list)
    student_activity: list[str] = Field(default_factory=list)
    assessment: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    unit_id: str | None = None


class LessonPlanSpec(IRBaseModel):
    schema_version: str = "0.1"
    plan_id: str
    title: str
    audience: str
    objectives: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    difficulties: list[str] = Field(default_factory=list)
    preparation: list[str] = Field(default_factory=list)
    sections: list[LessonPlanSection] = Field(default_factory=list)
    homework: list[str] = Field(default_factory=list)
    reflection: list[str] = Field(default_factory=list)
    teacher_only_notes: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class InteractiveQuestion(IRBaseModel):
    question_id: str
    unit_id: str
    prompt: str
    question_type: Literal["single_choice", "open_text", "reflection"]
    options: list[str] = Field(default_factory=list)
    correct_answer: str | None = None
    answer_explanation: str = ""
    feedback_correct: str = "回答正确。"
    feedback_retry: str = "请回看相关知识点后再试一次。"
    evidence_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_answer(self) -> "InteractiveQuestion":
        if self.question_type == "single_choice" and (not self.options or self.correct_answer not in self.options):
            raise ValueError("single_choice questions require a correct answer from options")
        return self


class InteractiveSpec(IRBaseModel):
    schema_version: str = "0.1"
    plan_id: str
    title: str
    objective: str
    questions: list[InteractiveQuestion] = Field(default_factory=list)
    completion_condition: str = "完成全部活动并查看反馈"
    accessibility: dict[str, Any] = Field(default_factory=lambda: {"keyboard": True, "touch": True, "labels": True})
    source_refs: list[str] = Field(default_factory=list)


class ArtifactFile(IRBaseModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    artifact_type: Literal["pptx", "docx", "html", "preview", "quality", "manifest", "other"]

    _path_is_relative = field_validator("path")(_validate_relative_path)


class ArtifactManifest(IRBaseModel):
    manifest_version: str = "0.1"
    plan_id: str
    package_id: str
    package_version: str
    generated_at: datetime = Field(default_factory=utc_now)
    generator_version: str = GENERATOR_VERSION
    files: list[ArtifactFile] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: Literal["complete", "partial", "failed"] = "complete"


class EvaluationIssue(IRBaseModel):
    issue_id: str
    issue_type: Literal[
        "missing_evidence",
        "wrong_content",
        "low_confidence",
        "duplicate_frame",
        "boundary",
        "formula",
        "chart",
        "answer_leak",
        "overflow",
        "broken_asset",
        "other",
    ]
    severity: Literal["error", "warning", "suggestion"]
    stage: Literal["keyframe", "ASR", "OCR", "vision", "alignment", "IR", "planning", "rendering"]
    target_id: str
    description: str
    expected: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    status: Literal["open", "confirmed", "fixed", "wont_fix"] = "open"


class EvaluationReport(IRBaseModel):
    schema_version: str = "0.1"
    report_id: str
    package_id: str
    package_version: str
    run_label: Literal["baseline", "candidate", "golden"] = "candidate"
    created_at: datetime = Field(default_factory=utc_now)
    metrics: dict[str, float] = Field(default_factory=dict)
    issues: list[EvaluationIssue] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: Literal["ok", "warning", "error"] = "ok"


def schema_bundle() -> dict[str, dict[str, Any]]:
    # Keep the parser contract in the same export surface as the intermediate
    # contracts without creating an import cycle at module import time.
    from .schemas import VideoParseResult
    from .video_understanding import video_understanding_json_schema

    return {
        "video_parse_result": VideoParseResult.model_json_schema(),
        "video_understanding_result": video_understanding_json_schema(),
        "teaching_content_ir": TeachingContentIR.model_json_schema(),
        "teaching_content_manifest": TeachingContentManifest.model_json_schema(),
        "demo_generation_plan": DemoGenerationPlan.model_json_schema(),
        "slide_deck_spec": SlideDeckSpec.model_json_schema(),
        "lesson_plan_spec": LessonPlanSpec.model_json_schema(),
        "interactive_spec": InteractiveSpec.model_json_schema(),
        "evaluation_report": EvaluationReport.model_json_schema(),
    }
