"""Pydantic 数据模型 — 所有 API 的请求/响应 schema。

本文件是后端、AI 模块、前端之间的唯一数据契约，对齐 docs/01_技术协议与接口规范。
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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


# ═══════════════════════════════════════════════════════════════
# 会话
# ═══════════════════════════════════════════════════════════════

class SessionCreate(BaseModel):
    teacher_name: str = ""
    subject: str = ""


class SessionInfo(BaseModel):
    session_id: str
    teacher_name: str
    subject: str
    status: str = "active"
    created_at: datetime


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


# ═══════════════════════════════════════════════════════════════
# 系统
# ═══════════════════════════════════════════════════════════════

class HealthResponse(BaseModel):
    app: str
    version: str
    environment: str
    status: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody
