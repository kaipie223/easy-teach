"""M1 — 教学意图分析引擎

调用 DeepSeek-V3 API 分析教师消息，输出结构化的教学意图。
"""

import json
import logging

from openai import OpenAI

from config import settings
from schemas import IntentResult, KnowledgePoint

logger = logging.getLogger(__name__)

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


class IntentAnalyzer:
    """意图分析器 — 单例，后端启动时初始化一次。"""

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
            return self._parse_intent(data)
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
        """教师确认后锁定意图。此处可扩展为从 DB 读取已确认的意图。"""
        return IntentResult(is_complete=True)

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


def get_intent_analyzer() -> IntentAnalyzer:
    global _intent_analyzer
    if _intent_analyzer is None:
        _intent_analyzer = IntentAnalyzer()
    return _intent_analyzer


async def analyze_intent(message: str, history: list[dict]) -> dict:
    """便捷函数 — 调用意图分析器。"""
    analyzer = get_intent_analyzer()
    result = analyzer.analyze("", history + [{"role": "user", "content": message}])
    return result.model_dump()
