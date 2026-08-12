"""M1 — 教学意图理解引擎"""

import asyncio
from config import settings
from schemas import IntentResult, KnowledgePoint

logger = logging.getLogger(__name__)

# ── 模块级意图缓存 ────────────────────────────────────────
# 存储每个 session 最近一次分析出的完整 IntentResult。
# 后续 lock_intent() 从此缓存读取，供课件生成使用。
_intent_cache: dict[str, IntentResult] = {}


def _cache_intent(session_id: str, result: IntentResult) -> None:
    """将意图结果存入缓存。"""
    _intent_cache[session_id] = result
    logger.info("Intent cached for session %s (complete=%s)", session_id, result.is_complete)


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
3. knowledge_points 至少包含 2-5 个知识点。
"""

_analyzer = IntentAnalyzer(
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
)


    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            if not settings.deepseek_api_key:
                raise ValueError("DEEPSEEK_API_KEY 未配置，请在 .env 文件中设置")
            self._client = OpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
            )
        return self._client

    def analyze(self, session_id: str, messages: list[dict]) -> IntentResult:
        """分析对话历史，返回结构化意图。"""
        if not settings.deepseek_api_key:
            return IntentResult(
                teaching_goal=messages[-1]["content"] if messages else "",
                missing_info=["API Key 未配置"],
                follow_up_question="请配置 DeepSeek API Key 后重试（在 .env 文件中设置 DEEPSEEK_API_KEY）",
            )
        try:
            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                    *messages,
                ],
                temperature=0.3,
                max_tokens=2000,
            )
            raw = response.choices[0].message.content.strip()
            # 去掉可能的 markdown 代码块标记
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]
                if raw.endswith("```"):
                    raw = raw[:-3]
            data = json.loads(raw)
            result = self._parse_intent(data)
            _cache_intent(session_id, result)  # 缓存成功的意图分析结果
            return result
        except json.JSONDecodeError:
            logger.warning("Failed to parse intent JSON, returning raw response")
            return IntentResult(
                teaching_goal=raw,
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
        """教师确认后锁定意图 — 优先从缓存读取，降级从数据库聊天记录重建。

        课件生成环节调用此方法获取已确认的教学意图，
        保证课件内容基于真实分析结果，而非空壳默认值。
        """
        # 1. 优先从内存缓存读取（同一进程内的最近分析结果）
        cached = _intent_cache.get(session_id)
        if cached and cached.is_complete:
            logger.info("Intent locked from cache for session %s", session_id)
            return cached
        if cached:
            logger.info("Intent found in cache but incomplete for session %s", session_id)
            return cached

        # 2. 缓存未命中 — 从数据库聊天记录重建（服务重启后恢复）
        try:
            from db.database import SessionLocal
            from models.session import ChatMessage

            db = SessionLocal()
            try:
                messages = (
                    db.query(ChatMessage)
                    .filter(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.created_at.asc())
                    .all()
                )
                if messages:
                    history = [
                        {"role": m.role, "content": m.content} for m in messages
                    ]
                    result = self.analyze(session_id, history)
                    logger.info("Intent rebuilt from DB for session %s", session_id)
                    return result
            finally:
                db.close()
        except Exception:
            logger.exception("Failed to rebuild intent from DB for session %s", session_id)

        # 3. 兜底 — 返回空的未完成意图，让下游用默认值继续
        logger.warning("No intent found for session %s, returning empty fallback", session_id)
        return IntentResult(
            is_complete=True,
            missing_info=["意图数据丢失，使用默认参数生成"],
        )

    def _parse_intent(self, data: dict) -> IntentResult:
        kps = [
            KnowledgePoint(
                order=k.get("order", i + 1),
                title=k.get("title", ""),
                difficulty=k.get("difficulty", "basic"),
                key_points=k.get("key_points", []),
                examples=k.get("examples", []),
                estimated_minutes=k.get("estimated_minutes", 10),
            )
            for i, k in enumerate(data.get("knowledge_points", []))
        ]
        return IntentResult(
            teaching_goal=data.get("teaching_goal", ""),
            target_audience=data.get("target_audience", ""),
            duration_minutes=data.get("duration_minutes", 45),
            knowledge_points=kps,
            logic_flow=data.get("logic_flow", []),
            style_preference=data.get("style_preference", ""),
            missing_info=data.get("missing_info", []),
            follow_up_question=data.get("follow_up_question"),
            confirm_summary=data.get("confirm_summary"),
            is_complete=data.get("is_complete", False),
        )


_intent_analyzer: IntentAnalyzer | None = None


def get_raw_intent(session_id: str) -> dict:
    return _analyzer.get_raw_intent(session_id)


def lock_intent(session_id: str) -> dict:
    result = _analyzer.lock_intent(session_id)
    return result.model_dump()
