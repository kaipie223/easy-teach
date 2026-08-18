"""Teaching intent extraction service.

The service keeps a small in-process cache for the current generation flow.
Database-backed message reconstruction remains the fallback after a restart.
"""

from __future__ import annotations

import json
import logging
import re

from openai import OpenAI

from backend.config import settings
from backend.schemas import IntentResult, KnowledgePoint

logger = logging.getLogger(__name__)

_intent_cache: dict[str, IntentResult] = {}

INTENT_SYSTEM_PROMPT = """你是一个教学意图分析助手。根据教师的备课对话，提取结构化的教学意图。

请严格按以下 JSON Schema 返回（只返回 JSON，不要其他内容）：
{
  "teaching_goal": "一句话教学目标",
  "target_audience": "授课对象（如：大一新生、小学三年级）",
  "duration_minutes": 45,
  "knowledge_points": [
    {"order": 1, "title": "知识点名称", "difficulty": "basic|intermediate|advanced", "key_points": ["要点1", "要点2"], "examples": [], "estimated_minutes": 15}
  ],
  "logic_flow": ["导入", "新授", "练习", "总结"],
  "style_preference": "严谨学术|生动活泼|案例驱动|互动探究",
  "missing_info": ["缺失的信息字段"],
  "follow_up_question": null,
  "confirm_summary": null,
  "is_complete": false
}

规则：
1. 如果教师信息不完整（缺少学科、年级、课时等），is_complete=false，在 missing_info 中列出，并生成一句追问。
2. 如果信息完整，is_complete=true，在 confirm_summary 中生成确认总结。
3. knowledge_points 至少包含 2-5 个知识点；无法判断时保留已有信息并继续追问。
"""


def _cache_intent(session_id: str, result: IntentResult) -> None:
    _intent_cache[session_id] = result
    logger.info("Intent cached for session %s (complete=%s)", session_id, result.is_complete)


def _extract_json(text: str) -> dict:
    """Parse JSON returned directly or inside a markdown code block."""
    raw = (text or "").strip()
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL | re.IGNORECASE)
    if match:
        raw = match.group(1).strip()
    return json.loads(raw)


class IntentAnalyzer:
    """Extract and cache a structured teaching intent."""

    def __init__(self):
        self._client: OpenAI | None = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            if not settings.deepseek_api_key:
                raise ValueError("DEEPSEEK_API_KEY 未配置，请在 .env 文件中设置")
            self._client = OpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
            )
        return self._client

    def analyze(self, session_id: str, messages: list[dict]) -> IntentResult:
        """Analyze the supplied conversation turn and return a validated result."""
        if not settings.deepseek_api_key:
            result = IntentResult(
                teaching_goal=messages[-1].get("content", "") if messages else "",
                missing_info=["API Key 未配置"],
                follow_up_question="请配置 DeepSeek API Key 后重试（在 .env 文件中设置 DEEPSEEK_API_KEY）",
            )
            _cache_intent(session_id, result)
            return result

        raw_text = ""
        try:
            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                    *messages,
                ],
                temperature=0.3,
                max_tokens=2000,
                response_format={"type": "json_object"},
                timeout=30,
            )
            raw_text = response.choices[0].message.content or ""
            result = self._parse_intent(_extract_json(raw_text))
            _cache_intent(session_id, result)
            return result
        except json.JSONDecodeError:
            logger.warning("Failed to parse intent JSON")
            return IntentResult(
                teaching_goal=raw_text,
                missing_info=["无法解析结构化意图，请重新描述"],
                follow_up_question="能否再详细描述一下您的备课需求？",
            )
        except Exception:
            logger.exception("Intent analysis error")
            return IntentResult(
                missing_info=["意图分析服务暂时不可用"],
                follow_up_question="请稍后重试，或直接告诉我您想备什么课？",
            )

    def lock_intent(self, session_id: str) -> IntentResult:
        """Return the most recent intent, rebuilding it from persisted messages if needed."""
        try:
            from backend.db.database import SessionLocal
            from backend.models.brief import TeachingBrief
            from backend.services.brief import brief_to_intent

            db = SessionLocal()
            try:
                confirmed = (
                    db.query(TeachingBrief)
                    .filter(
                        TeachingBrief.session_id == session_id,
                        TeachingBrief.status == "confirmed",
                    )
                    .order_by(TeachingBrief.version.desc())
                    .first()
                )
                if confirmed is not None:
                    result = brief_to_intent(confirmed)
                    _cache_intent(session_id, result)
                    return result
            finally:
                db.close()
        except Exception:
            logger.exception("Failed to load confirmed brief for session %s", session_id)

        cached = _intent_cache.get(session_id)
        if cached is not None:
            return cached

        try:
            from backend.db.database import SessionLocal
            from backend.models.session import ChatMessage

            db = SessionLocal()
            try:
                messages = (
                    db.query(ChatMessage)
                    .filter(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.created_at.asc())
                    .all()
                )
                if messages:
                    result = self.analyze(
                        session_id,
                        [{"role": item.role, "content": item.content} for item in messages],
                    )
                    logger.info("Intent rebuilt from DB for session %s", session_id)
                    return result
            finally:
                db.close()
        except Exception:
            logger.exception("Failed to rebuild intent from DB for session %s", session_id)

        logger.warning("No intent found for session %s, returning empty fallback", session_id)
        result = IntentResult(
            is_complete=True,
            missing_info=["意图数据丢失，使用默认参数生成"],
        )
        _cache_intent(session_id, result)
        return result

    def update_intent(self, session_id: str, modification_text: str) -> IntentResult:
        """Apply a free-form modification through the same extraction path."""
        return self.analyze(session_id, [{"role": "user", "content": modification_text}])

    @staticmethod
    def _parse_intent(data: dict) -> IntentResult:
        raw_points = data.get("knowledge_points") or []
        knowledge_points = [
            KnowledgePoint(
                order=item.get("order", index + 1),
                title=item.get("title", ""),
                difficulty=item.get("difficulty", "basic"),
                key_points=item.get("key_points") or [],
                examples=item.get("examples") or [],
                estimated_minutes=item.get("estimated_minutes", 10),
            )
            for index, item in enumerate(raw_points)
            if isinstance(item, dict)
        ]
        return IntentResult(
            teaching_goal=data.get("teaching_goal", ""),
            target_audience=data.get("target_audience", ""),
            duration_minutes=data.get("duration_minutes", 45),
            knowledge_points=knowledge_points,
            logic_flow=data.get("logic_flow") or [],
            style_preference=data.get("style_preference", ""),
            missing_info=data.get("missing_info") or [],
            follow_up_question=data.get("follow_up_question"),
            confirm_summary=data.get("confirm_summary"),
            is_complete=bool(data.get("is_complete", False)),
        )


_intent_analyzer: IntentAnalyzer | None = None


def get_intent_analyzer() -> IntentAnalyzer:
    global _intent_analyzer
    if _intent_analyzer is None:
        _intent_analyzer = IntentAnalyzer()
    return _intent_analyzer


def get_raw_intent(session_id: str) -> dict:
    result = get_intent_analyzer().lock_intent(session_id)
    return result.model_dump()


def lock_intent(session_id: str) -> dict:
    return get_intent_analyzer().lock_intent(session_id).model_dump()
