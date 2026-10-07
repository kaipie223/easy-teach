"""共享的 JSON 补全调用：输出预算、思考模式与降级策略只有这一份。

课程蓝图流水线（``courseware_ai``）与局部重生成（``revision_ai``）以前各写一份
``_completion``，两份都把 ``max_tokens`` 写死在很小的值上；更麻烦的是"调大之后被
服务端拒绝"这件事没有落点——失败会一路冒到教师面前的"生成失败"。

把调用收拢到这里之后：预算与降级只有一处定义，两个调用方不必各改一遍，也不会出现
"改了一处忘了另一处"的漂移。
"""

from __future__ import annotations

import logging

from openai import APIStatusError, OpenAI

from backend.config import settings

logger = logging.getLogger(__name__)

# max_tokens 被服务端拒绝时的回退值。它的意义只是"调大失败时不要把整条链路打死"，
# 不是内容预算——正常路径永远按配置给足的预算走。
FALLBACK_MAX_TOKENS = 8000


def _rejection_text(error: APIStatusError) -> str:
    body = getattr(error, "message", "") or ""
    return f"{body} {error}".lower()


def _rejects_token_budget(error: APIStatusError) -> bool:
    text = _rejection_text(error)
    return "max_tokens" in text or "max output" in text or "output limit" in text


def _rejects_thinking(error: APIStatusError) -> bool:
    text = _rejection_text(error)
    return "thinking" in text or "reasoning" in text


def json_completion(
    client: OpenAI,
    messages: list[dict[str, str]],
    *,
    model: str,
    max_tokens: int | None = None,
    timeout: int | None = None,
    thinking: bool | None = None,
):
    """发一次 JSON 模式的对话补全：预算按配置给足，被拒时逐项降级。

    降级只在错误**明确指向那个参数**时才发生；其他 4xx（鉴权、配额、模型名）照常
    抛出——用重试掩盖真实故障，比直接失败更难排查。
    """
    limit = max(1, int(max_tokens or settings.deepseek_max_output_tokens))
    wait = max(1, int(timeout or settings.deepseek_request_timeout_seconds))
    use_thinking = (
        bool(settings.deepseek_thinking_enabled) if thinking is None else bool(thinking)
    )
    lowered_tokens = False
    lowered_thinking = False

    while True:
        try:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.35,
                max_tokens=limit,
                response_format={"type": "json_object"},
                extra_body={
                    "thinking": {"type": "enabled" if use_thinking else "disabled"}
                },
                timeout=wait,
            )
        except APIStatusError as exc:
            # 两个开关各只降一次，所以最多三轮，不存在无限重试。
            if use_thinking and not lowered_thinking and _rejects_thinking(exc):
                lowered_thinking = True
                use_thinking = False
                logger.warning("服务端不接受思考模式，已关闭后重试：%s", exc)
                continue
            if (
                limit > FALLBACK_MAX_TOKENS
                and not lowered_tokens
                and _rejects_token_budget(exc)
            ):
                lowered_tokens = True
                limit = FALLBACK_MAX_TOKENS
                logger.warning(
                    "输出预算被服务端拒绝，回退到 %s 后重试：%s",
                    FALLBACK_MAX_TOKENS,
                    exc,
                )
                continue
            raise
