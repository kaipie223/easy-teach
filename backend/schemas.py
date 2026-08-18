"""Pydantic 数据模型 — 所有 API 的请求/响应 schema。

本文件是后端、AI 模块、前端之间的唯一数据契约，对齐 docs/01_技术协议与接口规范。
"""

from datetime import datetime
from enum import Enum
from typing import Any

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


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


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
    teaching_goal: str = ""
    target_audience: str = ""
    duration_minutes: int = 45
    knowledge_points: list[KnowledgePoint] = Field(default_factory=list)
    logic_flow: list[str] = Field(default_factory=list)
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


# ═══════════════════════════════════════════════════════════════
# M5 课件生成
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
    status: TaskStatus
    progress: int = 0
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
