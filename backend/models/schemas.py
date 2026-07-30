"""Pydantic 数据模型 — 所有 API 的请求/响应 schema"""

from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


# ── 枚举 ───────────────────────────────────────────

class Subject(str, Enum):
    CHINESE = "语文"
    MATH = "数学"
    ENGLISH = "英语"
    PHYSICS = "物理"
    CHEMISTRY = "化学"
    BIOLOGY = "生物"
    HISTORY = "历史"
    GEOGRAPHY = "地理"
    POLITICS = "政治"


class Grade(str, Enum):
    PRIMARY = "小学"
    JUNIOR = "初中"
    SENIOR = "高中"


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# ── 对话 / 意图 ────────────────────────────────────

class ChatMessage(BaseModel):
    role: str = Field(..., description="user / assistant")
    content: str


class ChatRequest(BaseModel):
    session_id: str
    message: str
    history: list[ChatMessage] = Field(default_factory=list)


class IntentResult(BaseModel):
    """M1 输出 — 结构化教学意图"""
    subject: Subject | None = None
    grade: Grade | None = None
    topic: str = ""
    keywords: list[str] = Field(default_factory=list)
    lesson_type: str = ""          # 新课 / 复习课 / 习题课
    style: str = ""                # 严肃 / 活泼 / 互动
    confidence: float = 0.0
    missing_info: list[str] = Field(default_factory=list)   # AI 还需要追问的字段


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    intent: IntentResult | None = None


# ── 文件上传 / 解析 ────────────────────────────────

class ReferenceMaterial(BaseModel):
    """M2 输出 — 解析后的参考资料"""
    file_id: str
    file_name: str
    file_type: str                # pdf / docx / image / video
    extracted_text: str
    page_count: int = 1
    metadata: dict = Field(default_factory=dict)


class UploadResponse(BaseModel):
    file_id: str
    file_name: str
    status: str


# ── RAG 检索 ───────────────────────────────────────

class RAGDocument(BaseModel):
    """M3 输出 — 知识库检索结果"""
    doc_id: str
    content: str
    source: str
    score: float
    metadata: dict = Field(default_factory=dict)


# ── 课件生成 ───────────────────────────────────────

class GenerateRequest(BaseModel):
    """M5 输入 — 课件生成请求"""
    session_id: str
    intent: IntentResult
    references: list[str] = Field(default_factory=list)     # file_id 列表
    rag_docs: list[str] = Field(default_factory=list)       # doc_id 列表
    extra_instructions: str = ""


class GenerateTask(BaseModel):
    """M5 输出 — 异步生成任务"""
    task_id: str
    status: TaskStatus
    created_at: datetime
    pptx_url: str | None = None
    docx_url: str | None = None
    html_url: str | None = None


class FeedbackRequest(BaseModel):
    """M6 输入 — 修改意见"""
    task_id: str
    feedback: str


# ── 语音 ───────────────────────────────────────────

class SpeechResponse(BaseModel):
    text: str
    duration_seconds: float


class FeedbackResponse(BaseModel):
    task_id: str
    status: str


# ── 系统 / 错误响应 ───────────────────────────────────────────

class HealthResponse(BaseModel):
    app: str
    version: str
    environment: str
    status: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict | list | str | None = None
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody
