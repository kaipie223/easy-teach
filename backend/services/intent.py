"""M1 — 教学意图理解引擎"""

import asyncio
from config import settings
from ai.intent.analyzer import IntentAnalyzer

_analyzer = IntentAnalyzer(
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
)


async def analyze_intent(session_id: str, message: str, history: list[dict]) -> dict:
    messages = history + [{"role": "user", "content": message}]
    result = await asyncio.to_thread(_analyzer.analyze, session_id, messages)
    return result.model_dump()


def get_raw_intent(session_id: str) -> dict:
    return _analyzer.get_raw_intent(session_id)


def lock_intent(session_id: str) -> dict:
    result = _analyzer.lock_intent(session_id)
    return result.model_dump()
