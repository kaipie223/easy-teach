"""幻灯片主题层：色板、字阶、栅格与构图规格。

为什么单独一层：以前配色、字号、几何散落在 generator.py 的几百行绘制代码里，
"改设计"等于改几十处常量。现在所有版式都从这里取值，换主题只改这一个文件，
前端预览也读同一份语义（tokens.css 的 --slide-* 与这里的字段一一对应）。

默认主题 `academic_blue` 取自教师给的参考课件（去掉肉眼猜测，色值由
scripts/sample_theme_colors.py 从参考图直接取样）：主蓝 #315CA2 占封面 33.7%
面积，白字对它的对比度 6.71:1，是这类模板耐看的原因。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SlideTheme:
    """一套幻灯片视觉规格。字段名与前端 tokens.css 的 --slide-* 对应。"""

    name: str
    # ── 色板 ────────────────────────────────────────────
    primary: str          # 主蓝：色条、编号数字、强调
    primary_dark: str     # 深一档：浅底上的文字、悬停
    primary_light: str    # 浅一档：水印、次级图形
    tint: str             # 极浅蓝：徽标底、标签底
    surface: str          # 内容区浅灰底（投影下太浅会看不出分区）
    block: str            # 书签编号块的灰底
    line: str             # 细边框
    ink: str              # 正文黑
    muted: str            # 次要文字
    on_primary: str       # 压在主蓝上的文字
    accent: str           # 暖色强调（数据、对比右栏）
    # ── 字阶（pt） ───────────────────────────────────────
    display: float        # 封面/章节主标题
    title: float          # 内容页标题
    subtitle: float       # 导语
    body: float           # 正文
    label: float          # 条目名/标签
    micro: float          # 页脚、来源、注释
    # ── 栅格与构图（英寸） ────────────────────────────────
    margin: float         # 四周边距
    header_bar_height: float   # 顶部色条高度
    header_bar_width: float    # 顶部色条宽度（从左边距起算）
    title_top: float           # 标题基线（右上角标题）
    body_top: float            # 内容区顶
    body_height: float         # 内容区高
    footer_top: float          # 页脚
    block_height: float        # 书签块高度
    block_gap: float           # 书签块间距
    # 装饰用的暖色两档：以前写死在 generator.py 里（COLOR_WARM / COLOR_DECOR_WARM），
    # 是这一层"配色只有一份"的两个漏网之鱼 —— 前端预览要跟着走，就必须也在这里。
    warm: str = "F0700A"       # 暖色块（对比右栏、渐变的一半）
    warm_soft: str = "FFE7CC"  # 暖色的极浅档（封面装饰圆）
    card_radius: float = 0.06
    extras: dict = field(default_factory=dict)


ACADEMIC_BLUE = SlideTheme(
    name="academic_blue",
    primary="315CA2",
    primary_dark="24467C",
    primary_light="406CB6",
    tint="E8EEF8",
    surface="EDEDED",      # 比参考图的 F2F2F2 略深：投影与截图下才看得出分区
    block="E1E1E1",
    line="C8C8C8",
    ink="1A1A1A",
    muted="6B6B6B",
    on_primary="FFFFFF",
    accent="C2410C",
    display=40.0,
    title=28.0,
    subtitle=17.0,
    body=17.0,
    label=18.0,
    micro=11.0,
    margin=0.65,
    header_bar_height=0.34,
    header_bar_width=7.6,
    title_top=0.42,
    body_top=1.55,
    body_height=4.75,
    footer_top=6.85,
    block_height=0.72,
    block_gap=0.22,
)

SLIDE_THEMES: dict[str, SlideTheme] = {ACADEMIC_BLUE.name: ACADEMIC_BLUE}
DEFAULT_THEME = ACADEMIC_BLUE


def get_theme(name: str | None) -> SlideTheme:
    """按名字取主题；未知名字回落到默认，绝不因为配置写错而渲染失败。"""
    if not name:
        return DEFAULT_THEME
    return SLIDE_THEMES.get(str(name).strip(), DEFAULT_THEME)


# ── 主题 → 前端 CSS 变量的唯一映射 ──────────────────────────────
#
# 前端预览（SlidePreview.vue）用 var(--slide-*) 画幻灯片。以前这组变量在
# tokens.css 里手抄了一份，注释写着"改这一行要同时改 generator.py" —— 结果配色
# 搬进本层之后没人跟着改，于是**同一页在应用里预览是亮蓝、导出却是学术蓝**。
# 现在映射放在这里、由接口下发、前端启动时注入，两处不可能再各改一遍。
#
# 每个变量都指向 generator.py 里一个真实存在的 COLOR_* 常量，不是另发明一套颜色。
CSS_VARIABLES: dict[str, str] = {
    "--slide-primary": "primary",              # = COLOR_PRIMARY 主蓝
    "--slide-accent": "primary_dark",          # = COLOR_BAND 深一档的蓝
    "--slide-accent-soft-bg": "surface",       # = COLOR_PANEL 内容区浅灰底
    "--slide-accent-soft-border": "line",      # = COLOR_LINE 内容框细边框
    "--slide-chip-bg": "tint",                 # = COLOR_SURFACE 徽标/标签底
    "--slide-decor": "tint",                   # = COLOR_DECOR 封面装饰块
    "--slide-decor-warm": "warm_soft",         # = COLOR_DECOR_WARM 暖色装饰圆
    "--slide-warm": "warm",                    # = COLOR_WARM 暖色块
    "--slide-track": "block",                  # = COLOR_TRACK 进度条底槽
    "--slide-warn-text": "accent",             # = COLOR_ACCENT 暖色文字
}


def css_variables(theme: SlideTheme | None = None) -> dict[str, str]:
    """把主题翻译成前端要注入的 CSS 变量（值带 ``#``，可直接 setProperty）。"""
    target = theme or DEFAULT_THEME
    return {
        name: f"#{getattr(target, field_name)}"
        for name, field_name in CSS_VARIABLES.items()
    }
