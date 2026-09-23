"""Pydantic 数据模型 — 所有 API 的请求/响应 schema。

本文件是后端、AI 模块、前端之间的唯一数据契约，对齐 docs/01_技术协议与接口规范。
"""

import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ═══════════════════════════════════════════════════════════════
# 幻灯片文本标记
# ═══════════════════════════════════════════════════════════════

# 模型用 **重点** 标出需要强调的关键词。只有幻灯片要点有渲染器能把它变成加粗
# 变色，所以标记被限制在 slides[*].bullets[*]，其它位置在写入时就剥掉，避免
# 教案、打印版和互动页里出现裸星号。
EMPHASIS_PATTERN = re.compile(r"\*\*(.+?)\*\*")


def strip_emphasis(text: str) -> str:
    """Remove emphasis markup while keeping the text inside it."""
    return EMPHASIS_PATTERN.sub(r"\1", str(text))


def limit_emphasis_to_bullets(node: Any, *, in_bullets: bool = False) -> Any:
    """Recursively drop emphasis markup that sits outside slide bullets.

    The deck renders `**重点**` as bold + colour, while the lesson document, the
    printable handout and the interactive page are plain text — a marker leaking
    there would show up as literal asterisks.
    """
    if isinstance(node, dict):
        return {
            key: limit_emphasis_to_bullets(value, in_bullets=key == "bullets")
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [limit_emphasis_to_bullets(item, in_bullets=in_bullets) for item in node]
    if isinstance(node, str) and not in_bullets and "**" in node:
        return strip_emphasis(node)
    return node


# ═══════════════════════════════════════════════════════════════
# 枚举
# ═══════════════════════════════════════════════════════════════

class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageType(str, Enum):
    TEXT = "text"
    # Incremental model output. Deltas are transient: they are streamed to the
    # client for the typewriter effect but never persisted, so a reload shows the
    # consolidated `question` / `confirm` event instead of hundreds of fragments.
    DELTA = "delta"
    QUESTION = "question"
    CONFIRM = "confirm"
    ERROR = "error"


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
    PDF = "pdf"
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


class StageInfo(BaseModel):
    """One step of a long-running job: stable key, wording, completion percent.

    The wording travels with the stage instead of living in the client, so the
    frontend never keeps a second copy of the same table.
    """

    key: str
    label: str
    percent: int


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
    # 解析进度。按资料类型给出不同步骤（图片多一步视觉识别，视频多一步转录解析），
    # 所以步骤清单也一并下发，前端不需要知道哪个类型该走哪些步骤。
    stage: str | None = None
    stage_label: str | None = None
    stage_percent: int = 0
    stage_started_at: datetime | None = None
    stages: list[StageInfo] = Field(default_factory=list)
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
    owner_id: str
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
    point_id: str = Field(default="", max_length=40, pattern=r"^[A-Za-z0-9_-]*$")
    order: int
    title: str
    difficulty: str = "basic"
    key_points: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    estimated_minutes: int = 5


class TeachingBriefUpdate(BaseModel):
    # 学科与学段决定后面整份教学设计的组织方式，必须允许教师直接修正。
    course_name: str | None = Field(default=None, max_length=200)
    subject: str | None = Field(default=None, max_length=100)
    grade: str | None = Field(default=None, max_length=100)
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
    teaching_focus: str = ""
    teaching_difficulties: str = ""
    output_types: list[str] = Field(default_factory=list)
    interaction_ideas: str = ""
    focus_and_difficulties: str = ""
    style_preference: str = ""
    existing_knowledge: str = ""
    case_preference: str = ""
    homework_type: str = ""
    forbidden_content: str = ""
    scenario_extensions: str = ""
    extra_requirements: str = ""
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


# ═══════════════════════════════════════════════════════════════
# 幻灯片版式
# ═══════════════════════════════════════════════════════════════

# 版式名是快照的一部分，也是渲染器与前端预览判断"这一页长什么样"的唯一依据。
# 规范值只有下面这些；同义词在写入快照时就归一，所以前端只需要认识规范值本身，
# 不必再抄一份同义词表——两份表必然漂移，而漂移的后果就是预览与导出不一致。
CANONICAL_LAYOUTS = (
    "cover",    # 封面
    "agenda",   # 目录
    "section",  # 章节分隔
    "bullets",  # 标准讲解页，也是默认与兜底
    "steps",    # 有序步骤：要学生照着做
    "flow",     # 流程／因果：这个过程自己这样发生
    "cards",    # 并列卡片：同级、无先后
    "compare",  # 双栏对比
    "metric",   # 数据度量：值得放大的数字
    "quote",    # 引用原文
    "summary",  # 小结
)
LAYOUT_ALIASES = {
    "cover": "cover",
    "title": "cover",
    "title_slide": "cover",
    "agenda": "agenda",
    "toc": "agenda",
    "outline": "agenda",
    "section": "section",
    "section_header": "section",
    "divider": "section",
    "bullets": "bullets",
    "bullet": "bullets",
    "steps": "steps",
    "step": "steps",
    "process": "steps",
    "procedure": "steps",
    "flow": "flow",
    "flowchart": "flow",
    "flow_chart": "flow",
    "cause_effect": "flow",
    "cards": "cards",
    "card": "cards",
    "grid": "cards",
    "categories": "cards",
    "compare": "compare",
    "comparison": "compare",
    "two_column": "compare",
    "metric": "metric",
    "metrics": "metric",
    "numbers": "metric",
    "statistics": "metric",
    "quote": "quote",
    "quotation": "quote",
    "citation": "quote",
    "excerpt": "quote",
    "summary": "summary",
    "conclusion": "summary",
    "wrap_up": "summary",
    # 模板兜底路径（compile_plan_content）用这些名字表达页型，归一到最近的规范值：
    # 概览与知识点页都是标准讲解页，示例页是"先…再…最后…"的顺序步骤，误区页是
    # 双栏对比，流程页是因果流程，互动与迁移页退回到标准讲解页。
    "overview": "bullets",
    "knowledge": "bullets",
    "example": "steps",
    "misconception": "compare",
    "process": "flow",
    "interaction": "bullets",
    "application": "bullets",
}


def normalize_layout(value: str | None) -> str:
    """把已知的同义词换成规范版式名，不认识的值原样返回。

    不认识的值故意不改写：渲染器要靠"这个值不是我认识的"才能判断模型没有给出
    有效版式，进而退回封面／小结的位置骨架。若在这里一律落成默认版式，第 1 页
    和最后 1 页的兜底就失效了——`title_and_bullets` 这个默认值正是这种情况。

    连字符与空格按分隔符处理：模型很爱写 `two-column`、`flow chart`，它们和
    表里的下划线写法是同一个意思，不该因此掉到"未识别"去。
    """
    raw = str(value or "").strip().lower()
    raw = raw.replace("-", "_").replace(" ", "_")
    return LAYOUT_ALIASES.get(raw, raw)


class SlideImageSpec(BaseModel):
    """A picture attached to one slide.

    `material_id` points at a teacher-uploaded image in `materials`. Only the ID
    is stored: renderers receive the resolved file separately, so a snapshot
    never carries a filesystem path that would break when files move, and an ID
    is always checked against the owning project before anything is rendered.

    `placement` decides how the picture shares the 16:9 canvas with the text:

    - ``right``      — bullets left, picture right (the safe default)
    - ``full``       — picture fills the content area; bullets move to the notes
    - ``background`` — picture covers the whole slide behind a translucent sheet
    """

    material_id: str = Field(min_length=1, max_length=40)
    placement: Literal["right", "full", "background"] = "right"
    caption: str = ""


class SlideSpec(BaseModel):
    slide_id: str
    order: int
    title: str
    purpose: str
    # 这个默认值刻意不是规范版式名：它表示"模型没有指定版式"，渲染器据此对
    # 第 1 页和最后 1 页套用封面／小结骨架，而不是让整份课件退化成 N 页雷同的
    # 讲解页。写成一个真实版式名会让"未指定"与"明确指定"再也分不开。
    layout: str = "title_and_bullets"
    bullets: list[str] = Field(default_factory=list)
    speaker_notes: str = ""
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    # Optional so snapshots written before pictures existed still validate.
    image: SlideImageSpec | None = None

    @field_validator("layout")
    @classmethod
    def _canonical_layout(cls, value: str) -> str:
        """写入快照时就把版式名归一，前端因此不必再复制一份同义词表。"""
        return normalize_layout(value)


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
    explanation: str = ""
    feedback_correct: str = "回答正确，已经掌握本题要点。"
    feedback_incorrect: str = "答案还不完整，请结合解析再试一次。"
    score: int = Field(default=10, ge=1, le=100)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class PptxContentSpec(BaseModel):
    narrative_arc: list[str] = Field(default_factory=list)
    visual_direction: str = "清晰、克制、便于课堂投影"
    max_bullets_per_slide: int = Field(default=5, ge=2, le=8)
    speaker_notes_required: bool = True


class DocxContentSpec(BaseModel):
    teacher_preparation: list[str] = Field(default_factory=list)
    differentiation: list[str] = Field(default_factory=list)
    homework: str = ""
    reflection_prompts: list[str] = Field(default_factory=list)


class PdfContentSpec(BaseModel):
    printable_summary: str = ""
    assessment_checklist: list[str] = Field(default_factory=list)
    include_sources: bool = True


class HtmlContentSpec(BaseModel):
    interaction_ids: list[str] = Field(default_factory=list)
    completion_message: str = "练习完成，请结合解析回顾本课要点。"
    allow_retry: bool = True
    accessibility_notes: list[str] = Field(default_factory=list)


class OutputContentSpecs(BaseModel):
    pptx: PptxContentSpec = Field(default_factory=PptxContentSpec)
    docx: DocxContentSpec = Field(default_factory=DocxContentSpec)
    pdf: PdfContentSpec = Field(default_factory=PdfContentSpec)
    html: HtmlContentSpec = Field(default_factory=HtmlContentSpec)


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
    output_specs: OutputContentSpecs = Field(default_factory=OutputContentSpecs)
    generation_notes: list[str] = Field(default_factory=list)


class CoursewarePlanBuildRequest(BaseModel):
    force_rebuild: bool = False
    generation_mode: Literal["ai", "template"] = "ai"
    allow_template_fallback: bool = False


class CoursewarePlanRevisionRequest(BaseModel):
    base_plan_id: str = Field(min_length=1, max_length=40)
    content: CoursewarePlanSpec
    summary: str = Field(default="教师编辑教学蓝图", max_length=500)


class CoursewarePlanInfo(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

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
    generation_mode: Literal["ai", "template", "manual"] = "template"
    model_name: str | None = None
    prompt_version: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    created_at: datetime
    updated_at: datetime


class CoursewareGenerateRequest(BaseModel):
    plan_id: str | None = None
    # 幂等键刻意不接受客户端指定（多传会被忽略）：只有服务端知道这次生成用的是
    # 哪份蓝图快照，客户端能给的只有 "latest"，换蓝图后仍会命中旧任务。服务端按
    # project + 本次基准版本派生，见 routers/courseware.py。


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


class AIRegenerateRequest(BaseModel):
    base_version_id: str = Field(min_length=1, max_length=40)
    target_type: Literal["slide", "lesson_section", "interaction"]
    target_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(min_length=1, max_length=2000)
    # Optional anchor inside the target. `field` narrows the rewrite to a single
    # editable field and `index` to a single element of a list field, so a teacher
    # can annotate one bullet instead of the whole slide. Omitting both rewrites
    # the entire target, which is the behaviour clients had before previews.
    field: str | None = Field(default=None, max_length=64)
    index: int | None = Field(default=None, ge=0)


class SlideImageRequest(BaseModel):
    """Bind or clear the picture on one slide.

    `material_id` is optional because ``None`` is how a picture is removed; that
    is the only field in the revision surface where an explicit null is
    meaningful.
    """

    material_id: str | None = Field(default=None, max_length=40)
    placement: Literal["right", "full", "background"] = "right"
    caption: str = Field(default="", max_length=200)


class RestoreVersionRequest(BaseModel):
    summary: str | None = Field(default=None, max_length=500)


class ArtifactVersionInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    artifact_version_id: str
    user_id: str
    project_id: str
    source_plan_id: str
    base_version_id: str | None = None
    version: int
    status: ArtifactVersionStatus
    summary: str
    snapshot: CoursewarePlanSpec
    generation_mode: Literal["initial", "manual", "ai", "restore"] = "manual"
    model_name: str | None = None
    prompt_version: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
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
        default_factory=lambda: [
            ExportFormat.PPTX,
            ExportFormat.DOCX,
            ExportFormat.PDF,
            ExportFormat.HTML,
        ],
        min_length=1,
        max_length=4,
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
    # 每条导出记录只负责一个格式，所以没有步骤清单，只有进度条与文案
    stage: str | None = None
    stage_label: str | None = None
    stage_percent: int = 0
    # 进入当前阶段的时刻；整体已用时长看 started_at
    stage_started_at: datetime | None = None
    started_at: datetime | None = None
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
    # 阶段是唯一事实来源，progress 由它派生；stage_label 允许比步骤更细
    # （构建蓝图内部的子阶段才是"AI 现在在做什么"最具体的答案）。
    stage: str | None = None
    stage_label: str | None = None
    stage_started_at: datetime | None = None
    stages: list[StageInfo] = Field(default_factory=list)
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
    task_id: str = Field(min_length=1, max_length=80)
    feedback: str = Field(min_length=1, max_length=4000)


class FeedbackResponse(BaseModel):
    task_id: str
    status: str
    project_id: str
    patch_id: str
    requires_confirmation: bool = False


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


# ═══════════════════════════════════════════════════════════════
# AI 配图（成果编辑）
# ═══════════════════════════════════════════════════════════════

class SlideImageGenerateRequest(BaseModel):
    """成果编辑里"AI 生成配图"的请求。

    只需要一句提示词：生成结果会登记成项目图片资料，绑定到页面仍走现有的
    "应用配图并创建版本"接口，所以这里不重复接收 placement / caption。
    """

    prompt: str = Field(min_length=2, max_length=600)
