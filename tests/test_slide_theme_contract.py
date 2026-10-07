"""幻灯片主题的单一来源：后端主题层 ⇄ 前端预览的 CSS 变量。

背景（实测事故）：导出的 PPTX 由 ``services/slide_theme.py`` 渲染，而应用内预览用
``tokens.css`` 里手抄的一份 ``--slide-*`` 画。配色搬进主题层之后前端那份没人跟着改，
于是同一页在预览里是亮蓝、导出却是学术蓝 —— 教师看到的就是"样式改了却没有任何变化"。

这里把"两处必须一致"变成可执行的检查，而不是又一句会过期的注释。
"""

import re
from pathlib import Path

from backend.services.slide_theme import CSS_VARIABLES, DEFAULT_THEME, css_variables

ROOT = Path(__file__).resolve().parents[1]
TOKENS_CSS = ROOT / "frontend/src/styles/tokens.css"
SLIDE_PREVIEW = ROOT / "frontend/src/components/preview/SlidePreview.vue"

# 这两条是应用自己的预览外框（卡片描边与底色），画的是"应用界面"而不是幻灯片内容，
# 所以不由主题下发。
STATIC_VARIABLES = {"--slide-canvas-bg", "--slide-canvas-border"}


def _css_declarations(text: str) -> dict[str, str]:
    return {
        name: value.strip().lower()
        for name, value in re.findall(r"(--slide-[a-z-]+):\s*([^;]+);", text)
    }


def test_every_variable_points_at_a_real_theme_field():
    variables = css_variables()
    assert variables["--slide-primary"] == f"#{DEFAULT_THEME.primary}"
    assert variables["--slide-accent"] == f"#{DEFAULT_THEME.primary_dark}"
    for name, value in variables.items():
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), (name, value)


def test_theme_endpoint_is_public_and_serves_the_theme(client):
    """主题接口无需登录：登录页也要画预览，而它只有一组颜色、不含账号信息。"""
    response = client.get("/api/v1/slide-theme")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["theme"] == DEFAULT_THEME.name
    assert payload["variables"] == css_variables()


def test_tokens_fallback_matches_the_theme():
    """tokens.css 里的兜底值必须与主题一致：拉不到接口时也不该是另一个颜色。"""
    declarations = _css_declarations(TOKENS_CSS.read_text(encoding="utf-8"))
    for name, value in css_variables().items():
        assert name in declarations, f"tokens.css 缺少兜底值 {name}"
        assert declarations[name] == value.lower(), (
            f"{name} 与主题不一致：tokens.css={declarations[name]}，主题={value.lower()}"
            "（改主题只改 slide_theme.py，不要手改 tokens.css）"
        )


def test_preview_only_uses_theme_or_static_variables():
    """预览里出现的 ``--slide-*`` 必须是"主题下发的"或"静态外框"之一。

    新增一个预览变量却忘了让它跟主题走，会立刻在这里失败 —— 这正是当年漏掉的那一步。
    """
    used = set(
        re.findall(r"var\((--slide-[a-z-]+)\)", SLIDE_PREVIEW.read_text(encoding="utf-8"))
    )
    assert used, "预览里应当有 --slide-* 的使用，检查路径是否失效"
    unknown = used - set(CSS_VARIABLES) - STATIC_VARIABLES
    assert not unknown, f"这些变量既不由主题下发、也不在静态白名单里：{sorted(unknown)}"
