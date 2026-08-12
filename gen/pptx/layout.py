"""
M5 - PPT 样式与常量系统 (Owner: 赵钰洁)
统一管理全局颜色、字体大小、版式边距
"""
from pptx.util import Pt, Inches
from pptx.dml.color import RGBColor


# ── 1. 全局颜色定义库 ──
class ThemeColors:
    # 主题色：常用于大标题、核心强调部分 (深沉的科技蓝)
    PRIMARY = RGBColor(0, 82, 165)

    # 辅助色：常用于小标题、引用 (温和的青色)
    SECONDARY = RGBColor(41, 171, 226)

    # 文字色：主内容区文字 (深灰，比纯黑更护眼)
    TEXT_MAIN = RGBColor(64, 64, 64)
    TEXT_MUTED = RGBColor(128, 128, 128)

    # 警告/重点色：必须记住的考点 (暖红色)
    ACCENT_RED = RGBColor(220, 53, 69)

    # 背景色：浅灰底，避免全白刺眼
    BG_LIGHT = RGBColor(248, 249, 250)


# ── 2. 全局字号定义 ──
class FontSettings:
    NAME_CN = "微软雅黑"  # 中文默认字体
    NAME_EN = "Arial"  # 英文默认字体

    # 按照 PPT 设计规范划分字号
    SIZE_TITLE = Pt(44)  # 封面大标题
    SIZE_SUBTITLE = Pt(32)  # 封面副标题、目录标题
    SIZE_HEADING = Pt(36)  # 内容页主标题
    SIZE_BODY_LV1 = Pt(24)  # 正文第一层级
    SIZE_BODY_LV2 = Pt(20)  # 正文第二层级
    SIZE_FOOTER = Pt(14)  # 页脚、注释


# ── 3. 全局排版边距常量 ──
class LayoutMargins:
    # 定义标准 16:9 页面下的常规边距
    LEFT = Inches(1.0)
    RIGHT = Inches(1.0)
    TOP = Inches(1.2)
    BOTTOM = Inches(0.8)


def apply_text_style(run, font_size=FontSettings.SIZE_BODY_LV1, color=ThemeColors.TEXT_MAIN, is_bold=False):
    """
    一个极其实用的工具函数：一键给文本应用我们定义的样式
    """
    run.font.name = FontSettings.NAME_CN
    run.font.size = font_size
    run.font.color.rgb = color
    run.font.bold = is_bold
