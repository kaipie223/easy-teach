"""Pydantic 数据模型 — 所有 API 的请求/响应 schema。

本文件是后端、AI 模块、前端之间的唯一数据契约，对齐 docs/01_技术协议与接口规范。
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ═══════════════════════════════════════════════════════════════
# 枚举
# ═══════════════════════════════════════════════════════════════

class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageType(str, Enum):
    TEXT = "text"
    QUESTION = "question"
    CONFIRM = "confirm"


class FileType(str, Enum):
    PDF = "pdf"
    WORD = "word"
    PPT = "ppt"
    IMAGE = "image"
    VIDEO = "video"


class MaterialStatus(str, Enum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    ARCHIVED = "archived"


class AnalysisStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class KnowledgeIndexStatus(str, Enum):
    PENDING = "pending"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class MaterialUsageType(str, Enum):
    CONTENT_BASIS = "content_basis"
    KNOWLEDGE_STRUCTURE = "knowledge_structure"
    CASE_SOURCE = "case_source"
    VISUAL_STYLE = "visual_style"
    INTERACTION_ASSET = "interaction_asset"
    ARCHIVE_ONLY = "archive_only"


class MaterialTargetType(str, Enum):
    WHOLE_COURSE = "whole_course"
    KNOWLEDGE_POINT = "knowledge_point"
    SLIDE_TYPE = "slide_type"
    LESSON_SECTION = "lesson_section"


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ArtifactVersionStatus(str, Enum):
    READY = "ready"


class RevisionPatchStatus(str, Enum):
    PREVIEW = "preview"
    APPLIED = "applied"
    REJECTED = "rejected"


class ExportFormat(str, Enum):
    PPTX = "pptx"
    DOCX = "docx"
    HTML = "html"


class ExportStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class CoursewarePlanStatus(str, Enum):
    READY = "ready"


class UserRole(str, Enum):
    TEACHER = "teacher"
    ADMIN = "admin"


# ═══════════════════════════════════════════════════════════════
# 账号与认证
# ═══════════════════════════════════════════════════════════════

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=80)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    email: EmailStr
    display_name: str
    role: UserRole
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserInfo


# ═══════════════════════════════════════════════════════════════
# 项目
# ═══════════════════════════════════════════════════════════════

class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    scenario: str = Field(default="", max_length=200)


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    scenario: str | None = Field(default=None, max_length=200)


class ProjectInfo(BaseModel):
    project_id: str
    owner_id: str
    title: str
    scenario: str
    status: str
    session_id: str | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class AdminUserInfo(UserInfo):
    pass


# ═══════════════════════════════════════════════════════════════
# 会话
# ═══════════════════════════════════════════════════════════════

class SessionCreate(BaseModel):
    teacher_name: str = ""
    subject: str = ""
    project_id: str | None = None


class ChatMessageInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    user_id: str | None = None
    project_id: str | None = None
    role: MessageRole
    content: str
    msg_type: MessageType
    event_data: dict[str, Any] | None = None
    created_at: datetime


class SessionInfo(BaseModel):
    session_id: str
    teacher_name: str
    subject: str
    user_id: str | None = None
    project_id: str | None = None
    status: str = "active"
    created_at: datetime
    messages: list[ChatMessageInfo] = Field(default_factory=list)
    intent_state: str | None = None
    brief_id: str | None = None


# ═══════════════════════════════════════════════════════════════
# 对话（SSE）
# ═══════════════════════════════════════════════════════════════

class ChatRequest(BaseModel):
    message: str


class ChatEvent(BaseModel):
    event_type: MessageType
    content: str
    data: dict[str, Any] | None = None


# ═══════════════════════════════════════════════════════════════
# 文件上传
# ═══════════════════════════════════════════════════════════════

class FileInfo(BaseModel):
    file_id: str
    original_name: str
    file_type: FileType
    size_kb: float
    upload_time: datetime
    ref_description: str | None = None


class MaterialInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    material_id: str
    owner_id: str
    project_id: str | None = None
    session_id: str | None = None
    original_name: str
    file_type: FileType
    mime_type: str | None = None
    size_bytes: int = Field(ge=0)
    checksum_sha256: str
    status: MaterialStatus
    ref_description: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    deleted_at: datetime | None = None


class MaterialAnalysisInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    analysis_id: str
    material_id: str
    run_number: int
    parser_name: str
    parser_version: str | None = None
    status: AnalysisStatus
    text_content: str | None = None
    result_json: dict[str, Any] = Field(default_factory=dict)
    page_count: int | None = None
    slide_count: int | None = None
    duration_seconds: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class MaterialBindingInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    binding_id: str
    material_id: str
    project_id: str
    created_by: str | None = None
    usage_type: MaterialUsageType
    target_type: MaterialTargetType
    target_id: str | None = None
    teacher_instruction: str | None = None
    suggested_by_ai: bool
    confirmed_by_teacher: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None
    invalidated_at: datetime | None = None


class MaterialBindingCreate(BaseModel):
    usage_type: MaterialUsageType
    target_type: MaterialTargetType = MaterialTargetType.WHOLE_COURSE
    target_id: str | None = Field(default=None, max_length=128)
    teacher_instruction: str | None = Field(default=None, max_length=2000)
    suggested_by_ai: bool = False
    confirmed_by_teacher: bool = False


class MaterialBindingReplaceRequest(BaseModel):
    bindings: list[MaterialBindingCreate] = Field(default_factory=list, max_length=20)


class EvidenceInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    evidence_id: str
    material_id: str | None = None
    knowledge_document_id: str | None = None
    analysis_id: str | None = None
    source_type: str
    chunk_index: int
    locator_json: dict[str, Any] = Field(default_factory=dict)
    text: str
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    usage_tags: list[str] = Field(default_factory=list)
    content_hash: str
    is_valid: bool
    invalidated_at: datetime | None = None
    invalidation_reason: str | None = None
    created_at: datetime


class KnowledgeDocumentInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: str
    owner_id: str | None = None
    collection_id: str
    title: str
    source_path: str
    file_type: str
    version: int
    checksum_sha256: str
    enabled: bool
    index_status: KnowledgeIndexStatus
    index_namespace: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    indexed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None
    deleted_at: datetime | None = None
    chunk_count: int = 0


class KnowledgeDocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None


class KnowledgeIndexResponse(BaseModel):
    status: KnowledgeIndexStatus
    indexed_document_ids: list[str] = Field(default_factory=list)
    chunk_count: int = 0


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)


# ═══════════════════════════════════════════════════════════════
# 知识点
# ═══════════════════════════════════════════════════════════════

class KnowledgePoint(BaseModel):
    order: int
    title: str
    difficulty: str = "basic"
    key_points: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    estimated_minutes: int = 5


class TeachingBriefUpdate(BaseModel):
    teaching_goal: str | None = Field(default=None, max_length=1000)
    target_audience: str | None = Field(default=None, max_length=200)
    duration_minutes: int | None = Field(default=None, ge=1, le=480)
    knowledge_points: list[KnowledgePoint] | None = None
    logic_flow: list[str] | None = None
    teaching_focus: str | None = Field(default=None, max_length=1000)
    teaching_difficulties: str | None = Field(default=None, max_length=1000)
    output_types: list[str] | None = None
    interaction_ideas: str | None = Field(default=None, max_length=1000)
    style_preference: str | None = Field(default=None, max_length=200)
    existing_knowledge: str | None = Field(default=None, max_length=1000)
    case_preference: str | None = Field(default=None, max_length=1000)
    homework_type: str | None = Field(default=None, max_length=500)
    forbidden_content: str | None = Field(default=None, max_length=1000)
    scenario_extensions: str | None = Field(default=None, max_length=1000)
    extra_requirements: str | None = Field(default=None, max_length=1000)


class TeachingBriefInfo(BaseModel):
    brief_id: str
    user_id: str | None = None
    project_id: str | None = None
    session_id: str | None = None
    version: int
    status: str
    content: dict[str, Any] = Field(default_factory=dict)
    teaching_goal: str = ""
    target_audience: str = ""
    duration_minutes: int = 45
    knowledge_points: list[KnowledgePoint] = Field(default_factory=list)
    logic_flow: list[str] = Field(default_factory=list)
    teaching_focus: str = ""
    teaching_difficulties: str = ""
    output_types: list[str] = Field(default_factory=list)
    interaction_ideas: str = ""
    style_preference: str = ""
    missing_info: list[str] = Field(default_factory=list)
    is_complete: bool = False
    source_refs: dict[str, Any] = Field(default_factory=dict)
    confidence: dict[str, float] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None = None


class TeachingBriefConfirmRequest(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)


# ═══════════════════════════════════════════════════════════════
# M1 意图分析
# ═══════════════════════════════════════════════════════════════

class IntentResult(BaseModel):
    course_name: str = ""
    subject: str = ""
    grade: str = ""
    teaching_goal: str = ""
    target_audience: str = ""
    duration_minutes: int = 45
    knowledge_points: list[KnowledgePoint] = Field(default_factory=list)
    logic_flow: list[str] = Field(default_factory=list)
    focus_and_difficulties: str = ""
    style_preference: str = ""
    missing_info: list[str] = Field(default_factory=list)
    follow_up_question: str | None = None
    confirm_summary: str | None = None
    is_complete: bool = False


# ═══════════════════════════════════════════════════════════════
# M2 参考资料
# ═══════════════════════════════════════════════════════════════

class ReferenceMaterial(BaseModel):
    file_id: str
    file_type: FileType
    extracted_text: str
    key_topics: list[str] = Field(default_factory=list)
    format_notes: str | None = None


# ═══════════════════════════════════════════════════════════════
# M3 RAG 检索
# ═══════════════════════════════════════════════════════════════

class RAGDocument(BaseModel):
    content: str
    source: str
    score: float
    evidence_id: str | None = None
    document_id: str | None = None
    locator: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
# M4 教学蓝图
# ═══════════════════════════════════════════════════════════════

class EvidenceRef(BaseModel):
    evidence_id: str | None = None
    document_id: str | None = None
    source_type: str
    source_name: str
    locator: dict[str, Any] = Field(default_factory=dict)
    quote: str = ""
    score: float | None = None


class SlideSpec(BaseModel):
    slide_id: str
    order: int
    title: str
    purpose: str
    layout: str = "title_and_bullets"
    bullets: list[str] = Field(default_factory=list)
    speaker_notes: str = ""
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class LessonPlanSectionSpec(BaseModel):
    section_id: str
    order: int
    title: str
    duration_minutes: int = Field(ge=1, le=480)
    objective: str
    teacher_actions: list[str] = Field(default_factory=list)
    student_actions: list[str] = Field(default_factory=list)
    assessment: str = ""
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class InteractionSpec(BaseModel):
    interaction_id: str
    interaction_type: str = "classification"
    title: str
    prompt: str
    items: list[str] = Field(default_factory=list)
    answer_groups: dict[str, list[str]] = Field(default_factory=dict)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class CoursewarePlanSpec(BaseModel):
    title: str
    target_audience: str
    duration_minutes: int = Field(ge=1, le=480)
    teaching_goal: str
    knowledge_points: list[KnowledgePoint] = Field(default_factory=list)
    logic_flow: list[str] = Field(default_factory=list)
    teaching_focus: str = ""
    teaching_difficulties: str = ""
    style_preference: str = ""
    slides: list[SlideSpec] = Field(default_factory=list)
    lesson_sections: list[LessonPlanSectionSpec] = Field(default_factory=list)
    interactions: list[InteractionSpec] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    generation_notes: list[str] = Field(default_factory=list)


class CoursewarePlanBuildRequest(BaseModel):
    force_rebuild: bool = False


class CoursewarePlanInfo(BaseModel):
    plan_id: str
    user_id: str
    project_id: str
    brief_id: str
    version: int
    status: CoursewarePlanStatus
    title: str
    duration_minutes: int
    content: CoursewarePlanSpec
    source_refs: list[EvidenceRef] = Field(default_factory=list)
    notes: str = ""
    created_at: datetime
    updated_at: datetime


class CoursewareGenerateRequest(BaseModel):
    plan_id: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


# ═══════════════════════════════════════════════════════════════
# M5 版本、局部修改与导出
# ═══════════════════════════════════════════════════════════════

class RevisionOperation(BaseModel):
    op: Literal["replace", "insert", "delete", "move", "regenerate"]
    target_id: str | None = Field(default=None, max_length=128)
    target_type: Literal["slide", "lesson_section", "interaction"] | None = None
    field: str | None = Field(default=None, max_length=64)
    value: Any | None = None
    after_id: str | None = Field(default=None, max_length=128)
    instruction: str | None = Field(default=None, max_length=2000)


class RevisionInterpretRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    base_version_id: str | None = Field(default=None, max_length=40)


class RevisionApplyRequest(BaseModel):
    patch_id: str = Field(min_length=1, max_length=40)
    confirmed: bool = False


class RestoreVersionRequest(BaseModel):
    summary: str | None = Field(default=None, max_length=500)


class ArtifactVersionInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    artifact_version_id: str
    user_id: str
    project_id: str
    source_plan_id: str
    base_version_id: str | None = None
    version: int
    status: ArtifactVersionStatus
    summary: str
    snapshot: CoursewarePlanSpec
    quality_status: str = "pending"
    quality_report: dict[str, Any] | None = None
    created_at: datetime


class RevisionPatchInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    patch_id: str
    user_id: str
    project_id: str
    base_version_id: str
    created_version_id: str | None = None
    instruction: str
    scope: str
    target_ids: list[str] = Field(default_factory=list)
    operations: list[RevisionOperation] = Field(default_factory=list)
    cascade_check: list[str] = Field(default_factory=list)
    requires_confirmation: bool
    status: RevisionPatchStatus
    summary: str
    created_at: datetime
    applied_at: datetime | None = None


class ExportCreateRequest(BaseModel):
    artifact_version_id: str | None = Field(default=None, max_length=40)
    formats: list[ExportFormat] = Field(
        default_factory=lambda: [ExportFormat.PPTX, ExportFormat.DOCX, ExportFormat.HTML],
        min_length=1,
        max_length=3,
    )
    force: bool = False


class ExportInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    export_id: str
    user_id: str
    project_id: str
    artifact_version_id: str
    format: ExportFormat
    status: ExportStatus
    file_id: str | None = None
    file_name: str | None = None
    checksum_sha256: str | None = None
    size_bytes: int | None = None
    retry_count: int = 0
    max_retries: int = 2
    error: str | None = None
    download_url: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class ExportBatchInfo(BaseModel):
    exports: list[ExportInfo] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# 兼容旧版课件生成
# ═══════════════════════════════════════════════════════════════

class GenerationInstruction(BaseModel):
    session_id: str
    teaching_goal: str
    target_audience: str
    duration_minutes: int
    knowledge_points: list[KnowledgePoint]
    logic_flow: list[str]
    style_preference: str
    rag_context: list[RAGDocument] = Field(default_factory=list)
    reference_materials: list[ReferenceMaterial] = Field(default_factory=list)
    extra_requirements: str = ""


class GenerateRequest(BaseModel):
    session_id: str
    plan_id: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class OutputFile(BaseModel):
    file_id: str
    file_type: str
    file_name: str
    size_kb: float
    download_url: str


class TaskInfo(BaseModel):
    task_id: str
    session_id: str
    project_id: str | None = None
    plan_id: str | None = None
    artifact_version_id: str | None = None
    task_type: str = "generation"
    status: TaskStatus
    progress: int = 0
    retry_count: int = 0
    max_retries: int = 2
    error_code: str | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    outputs: list[OutputFile] | None = None
    error: str | None = None


# ═══════════════════════════════════════════════════════════════
# M6 反馈
# ═══════════════════════════════════════════════════════════════

class FeedbackRequest(BaseModel):
    task_id: str
    feedback: str


class FeedbackResponse(BaseModel):
    task_id: str
    status: str


# ═══════════════════════════════════════════════════════════════
# M4 语音
# ═══════════════════════════════════════════════════════════════

class SpeechResponse(BaseModel):
    text: str
    duration_seconds: float
    session_id: str | None = None


# ═══════════════════════════════════════════════════════════════
# 系统
# ═══════════════════════════════════════════════════════════════

class HealthResponse(BaseModel):
    app: str
    version: str
    environment: str
    status: str
    checks: dict[str, Any] = Field(default_factory=dict)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None
    request_id: str
    recoverable: bool = True
    suggested_action: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
