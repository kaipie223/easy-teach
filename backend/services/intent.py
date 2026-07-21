"""M1 — 教学意图理解引擎"""


async def analyze_intent(message: str, history: list[dict]) -> dict:
    """
    分析用户消息中的教学意图。

    Args:
        message: 用户最新消息
        history: 对话历史

    Returns:
        IntentResult 字典
    """
    # TODO: 调用 DeepSeek-V3 → 提取学科/年级/主题/风格
    # TODO: 判断是否需要追问（missing_info）
    raise NotImplementedError("M1 意图引擎 — 待陈澜实现")
