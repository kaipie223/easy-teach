"""M4 renderers driven by one CoursewarePlan JSON document."""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
import uuid
from math import ceil
from typing import Any

from backend.config import settings
from backend.schemas import (  # noqa: F401 - strip_emphasis re-exported
    CANONICAL_LAYOUTS,
    EMPHASIS_PATTERN,
    SCENE_KIND_EXPRESSION_PROPS,
    SCENE_PALETTE,
    normalize_layout,
    strip_emphasis,
)
from backend.services.expressions import (
    ExpressionError,
    referenced_names,
    to_javascript,
)
from backend.services.slide_theme import DEFAULT_THEME

logger = logging.getLogger(__name__)

# 16:9 canvas shared by the pptx renderer and the frontend slide preview.
SLIDE_WIDTH_IN = 13.333
SLIDE_HEIGHT_IN = 7.5
# Content area between the title band and the source/page footer.
#
# 构图取自教师给的参考课件：顶部粗色条（左）+ 右上角课程名 → 页面标题在内容框上方
# → 浅灰内容框（导语在框内顶部）→ 三栏页脚。几何全部由这几条常量推导，改一处全册生效。
CONTENT_LEFT_IN = 0.65
CONTENT_TOP_IN = 1.15
CONTENT_WIDTH_IN = SLIDE_WIDTH_IN - 2 * CONTENT_LEFT_IN
CONTENT_HEIGHT_IN = 5.30
CAPTION_HEIGHT_IN = 0.4
# 标题与导语都在内容框**内部**（参考课件就是这样：整块浅灰区自成页面），
# 面板已被压到底层，所以标题不会被盖住。
TITLE_TOP_IN = 1.28
TITLE_HEIGHT_IN = 0.58
LEAD_TOP_IN = 2.00
LEAD_HEIGHT_IN = 0.32
SOURCE_TOP_IN = 6.50
FOOTER_TOP_IN = 6.88
# 封面整版主蓝的高度：底部留一条白带放页脚（参考课件就是这样）
COVER_BAND_HEIGHT_IN = 6.88
IMAGE_PLACEMENTS = ("right", "full", "background")
# 内容框：参考课件用整块浅灰底 + 细边框，而不是圆角浅蓝卡片。
PANEL_BOXES: dict[str, tuple[float, float, float, float]] = {
    "right": (0.72, CONTENT_TOP_IN, 5.58, CONTENT_HEIGHT_IN),
    "full": (0.72, CONTENT_TOP_IN, 11.90, CONTENT_HEIGHT_IN),
    "background": (0.72, CONTENT_TOP_IN, 11.90, CONTENT_HEIGHT_IN),
}
# 框内留出内边距（并给导语让出位置）之后的正文框。`right` 把另一半让给配图。
TEXT_BOXES: dict[str, tuple[float, float, float, float]] = {
    "right": (1.02, 2.42, 4.98, 3.83),
    "full": (1.02, 2.42, 11.30, 3.83),
    "background": (1.02, 2.42, 11.30, 3.83),
}
# 正文目标字号与下限。
#
# 12pt 在 13.3in 宽的投影画布上偏小（教室后排读不清）。所以先按 14pt 排版，一页装
# 不下时**拆页**而不是继续缩字号：多一页只是多翻一次，字号太小则是全班都看不清。
# 12pt 只留作单条超长、拆无可拆时的兜底。
BODY_TARGET_SIZE = 14.0
BODY_MIN_SIZE = 12.0
# 页眉（课程名）与页码的字号：投影场景的可读下限，9pt 在教室后排等于没有
CHROME_FONT_SIZE = 11.0
# 估算用的行高与每行宽度系数：实际渲染行距是 1.18、字宽满打满算，
# 估算时留出余量（见 _fit_body_size），否则最后一行会压到内容框边框上。
BODY_LINE_SPACING = 1.32
BODY_WIDTH_SAFETY = 0.92
BODY_SPACE_AFTER = 14.0
# 这些版式把要点交给标准正文框渲染，能用"高度"估出该不该拆页；
# 卡片/流程/对比等版式各有自己的排布与容量，不适用同一套估算。
HEIGHT_AWARE_LAYOUTS = {"bullets", "agenda", "summary"}

# ── 视觉系统：全部取自主题层（slide_theme.py） ───────────────
#
# 幻灯片从空白版式逐块绘制，颜色必须显式给出：主题色会随打开它的模板变化，
# 课程的主色不该被别人的模板改掉。
#
# 这些名字保持不变（各处绘制照旧引用），但取值改由主题决定 —— 换主题只改
# slide_theme.py 一处；前端 tokens.css 的 --slide-* 与主题字段一一对应。
SLIDE_FONT = "Microsoft YaHei"
_THEME = DEFAULT_THEME
COLOR_INK = _THEME.ink
COLOR_MUTED = _THEME.muted
COLOR_PRIMARY = _THEME.primary               # 主蓝：色条、编号、强调
COLOR_PRIMARY_LIGHT = _THEME.primary_light   # 浅一档：封面水印、次级图形
COLOR_PRIMARY_DARK = _THEME.primary_dark     # 深一档：浅底上的文字
COLOR_ACCENT = _THEME.accent                 # 暖色（配浅底用）
COLOR_SURFACE = _THEME.tint                  # 极浅蓝：徽标/标签底
COLOR_LINE = _THEME.line                     # 细边框
COLOR_PANEL = _THEME.surface                 # 内容区浅灰底
COLOR_BAND = _THEME.primary_dark             # 深一档的蓝：色带/强调块
COLOR_ON_BAND = _THEME.on_primary
COLOR_ON_PRIMARY = _THEME.on_primary         # 压在主蓝上的文字（封面白字）
# 装饰与图形的派生色：全部取自主题，不再手写十六进制值
COLOR_WARM = _THEME.warm                     # 暖色块（对比右栏、渐变的一半）
COLOR_DECOR = _THEME.tint                    # 封面装饰块
COLOR_DECOR_WARM = _THEME.warm_soft          # 暖色装饰圆
COLOR_TRACK = _THEME.block                   # 进度条底槽
# 参考课件的构图：顶部粗色条 + 右上角大号章节标题 + 浅灰内容区 + 三栏页脚
HEADER_BAR_IN = _THEME.header_bar_height
HEADER_BAR_WIDTH_IN = _THEME.header_bar_width
HEADER_TITLE_TOP_IN = _THEME.title_top

DEFAULT_LAYOUT = "bullets"
# 一页正文的可读密度上限。
#
# 生成端已经不限制每页条数（写得多不再被判失败），渲染端必须承接"这一页装不下"：
# 超出容量的条目会拆成"（续）"页继续投影。以前这里是隐式的——超出 max_bullets 的
# 条目被直接丢弃，教师看到的是"我写了 12 条，导出只剩 5 条"。
SLIDE_BULLET_CAPACITY = 8
# 目录页是结构总览，一行一项，按内容区高度最多排下这些行。
AGENDA_ROW_CAPACITY = 6
# 封面要点区（英寸）：右栏副标题下方到页面下边距之间的空白带。
# 封面要克制，但绝不能像以前那样把要点整段丢掉——用户在成果编辑里
# "丰富第一页"新增的要点必须能出现在导出件上；余量不足时按字号估算，
# 装不下的条目由调用方并入讲稿，而不是消失。
COVER_BULLET_LEFT_IN = 5.15
COVER_BULLET_TOP_IN = 5.86
COVER_BULLET_BOTTOM_IN = 7.15
COVER_BULLET_WIDTH_IN = 7.45
# 封面改为右侧图文时，图片从这里开始铺到右边缘（左侧色带与其强调线保持纯色）。
COVER_IMAGE_LEFT_IN = 4.62
# 这些版式自己排布全部内容、不依赖正文占位符，带配图时必须退回标准页：卡片、
# 流程和大数字被右图挤成窄条，比没有版式更难看。
TEXT_ONLY_LAYOUTS = {
    "agenda",
    "steps",
    "flow",
    "cards",
    "compare",
    "metric",
    "quote",
    "summary",
}
# 版式名与同义词表都住在 schemas 里：它是前后端的唯一数据契约，快照写入时就
# 完成归一，所以这里不做第二次映射，前端也不必再抄一份。

# 并列卡片／流程／度量的内部栅格
CARD_GAP_IN = 0.32
FLOW_GAP_IN = 0.62
METRIC_GAP_IN = 0.36
QUOTE_BAR_W_IN = 0.085
# 卡片只在冒号足够靠前时才拆成"小标题 + 说明"：冒号出现在长句中部时，前半是
# 一整个从句而不是标题，拆出来会得到一个又长又不像标题的"小标题"。
CARD_LABEL_MAX_CHARS = 8
# 度量页只认"开头就是数字"的条目。认不出就退回普通短句——一个版式不该因为
# 文本形式不配合而渲染失败。
METRIC_PATTERN = re.compile(
    r"^\s*([+\-]?\d[\d.,]*\s*(?:%|％|℃|°C|度|倍|万|亿|分钟|小时|天|周|年|个|人|次|条|项|分|秒)?)"
)
# 引用页的出处：以破折号开头的最后一条会被抽出来单独排，不留在正文里。
ATTRIBUTION_PREFIXES = ("——", "—", "--")


def _plan_title(plan: dict) -> str:
    return str(plan.get("title") or plan.get("teaching_goal") or "教学课程")


def _legacy_slides(plan: dict) -> list[dict]:
    slides = [
        {
            "slide_id": "slide_001",
            "order": 1,
            "title": _plan_title(plan),
            "purpose": "课程导入",
            "bullets": [
                f"授课对象：{plan.get('target_audience', '')}",
                f"课程时长：{plan.get('duration_minutes', 45)} 分钟",
            ],
            "speaker_notes": "介绍课程目标。",
            "evidence_refs": [],
        }
    ]
    for index, point in enumerate(plan.get("knowledge_points", []), start=2):
        slides.append(
            {
                "slide_id": f"slide_{index:03d}",
                "order": index,
                "title": point.get("title", "知识点"),
                "purpose": "知识点讲解",
                "bullets": point.get("key_points") or [point.get("title", "")],
                "speaker_notes": "围绕知识点进行讲解。",
                "evidence_refs": [],
            }
        )
    return slides


def _slides(plan: dict) -> list[dict]:
    return plan.get("slides") or _legacy_slides(plan)


def _source_label(ref: dict) -> str:
    source = str(ref.get("source_name") or ref.get("source_type") or "来源")
    locator = ref.get("locator") or {}
    details = []
    for key in ("page", "slide", "timestamp", "paragraph"):
        if key in locator:
            details.append(f"{key} {locator[key]}")
    return f"{source} ({', '.join(details)})" if details else source


def _source_line(refs: list[dict]) -> str:
    labels = [_source_label(ref) for ref in refs[:3]]
    return "来源：" + "；".join(labels) if labels else "来源：暂无可用证据"


def _color(value: str):
    from pptx.dml.color import RGBColor

    return RGBColor.from_string(value)


def _set_cjk_typeface(font, name: str) -> None:
    """Point the latin, East Asian and complex-script typefaces at one font.

    `font.name` only writes `a:latin`, so PowerPoint renders Chinese with the
    theme's East Asian font and a single line ends up mixing two typefaces.
    python-pptx exposes no API for the other two, hence the raw XML.
    """
    from pptx.oxml.ns import qn

    rpr = getattr(font, "_rPr", None)
    if rpr is None:
        return
    try:
        for tag in ("a:latin", "a:ea", "a:cs"):
            element = rpr.find(qn(tag))
            if element is None:
                element = rpr.makeelement(qn(tag), {})
                rpr.append(element)
            element.set("typeface", name)
    except Exception:
        logger.warning("Could not set the East Asian typeface to %s", name)


def _add_textbox(
    slide,
    left,
    top,
    width,
    height,
    text,
    *,
    font_size=22,
    bold=False,
    color=COLOR_INK,
    font_name=SLIDE_FONT,
    align=None,
    line_spacing=None,
):
    from pptx.util import Pt

    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.word_wrap = True
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    frame.clear()
    paragraph = frame.paragraphs[0]
    paragraph.text = str(text)
    paragraph.font.size = Pt(font_size)
    paragraph.font.bold = bold
    paragraph.font.name = font_name
    paragraph.font.color.rgb = _color(color)
    if line_spacing is not None:
        paragraph.line_spacing = line_spacing
    _set_cjk_typeface(paragraph.font, font_name)
    if align is not None:
        paragraph.alignment = align
    return box


def _add_block(slide, left, top, width, height, fill: str, *, oval: bool = False, kind=None):
    """A flat colour block. Slides start from a blank layout, so every piece of
    decoration has to be a real shape."""
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches

    shape = slide.shapes.add_shape(
        kind or (MSO_SHAPE.OVAL if oval else MSO_SHAPE.RECTANGLE),
        Inches(left),
        Inches(top),
        Inches(width),
        Inches(height),
    )
    shape.line.fill.background()
    shape.fill.solid()
    shape.fill.fore_color.rgb = _color(fill)
    try:
        shape.shadow.inherit = False
    except Exception:
        pass
    return shape


def _send_to_back(shape) -> None:
    """把形状移到最底层。

    python-pptx 按添加顺序叠放，而各版式都是"先写标题、再画面板"，标题一旦落在
    面板范围内就会被面板盖住。改绘制顺序要动所有版式，把面板压到底层只需动一处。
    """
    tree = shape._element.getparent()
    tree.remove(shape._element)
    # 索引 2：跳过 nvGrpSpPr 与 grpSpPr，插到所有图形之前
    tree.insert(2, shape._element)


def _add_panel(
    slide,
    box: tuple[float, float, float, float],
    *,
    fill: str = COLOR_SURFACE,
    line: str | None = COLOR_LINE,
):
    """内容框：平面浅灰底 + 细边框。

    参考课件用的是"整块浅灰平面 + 1px 细边框"（文档式分区），而不是圆角卡片；
    圆角卡片看久了像网页组件，平面分区更像讲义页面。
    """
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches

    left, top, width, height = box
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(left),
        Inches(top),
        Inches(width),
        Inches(height),
    )
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = _color(line)
        shape.line.width = Inches(0.75 / 72)  # 0.75pt 细描边
    shape.fill.solid()
    shape.fill.fore_color.rgb = _color(fill)
    try:
        shape.shadow.inherit = False
    except Exception:
        pass
    # 压到底层：面板只是底板，绝不能盖住标题或正文
    _send_to_back(shape)
    return shape


def _add_lead_in(slide, text: str) -> None:
    """One-line lead-in under the title.

    `purpose` already exists on every slide and was previously dropped on content
    pages, which is part of why they read as bare title-plus-list.
    """
    from pptx.util import Inches

    if not text.strip():
        return
    _add_textbox(
        slide,
        Inches(0.95),
        Inches(LEAD_TOP_IN),
        Inches(11.35),
        Inches(LEAD_HEIGHT_IN),
        strip_emphasis(text),
        font_size=_lead_size(text, width_in=11.35, height_in=LEAD_HEIGHT_IN),
        color=COLOR_MUTED,
    )


def _set_shape_text(shape, text: str, *, size: float, bold: bool, color: str) -> None:
    """Centre a short label inside a shape, used by the agenda badges."""
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Pt

    frame = shape.text_frame
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    paragraph = frame.paragraphs[0]
    paragraph.text = str(text)
    paragraph.alignment = PP_ALIGN.CENTER
    paragraph.font.size = Pt(size)
    paragraph.font.bold = bold
    paragraph.font.name = SLIDE_FONT
    paragraph.font.color.rgb = _color(color)
    _set_cjk_typeface(paragraph.font, SLIDE_FONT)


def _slide_layout(spec: dict, index: int, total: int) -> str:
    """Resolve the page layout.

    An explicit choice always wins. Otherwise a minimal skeleton is applied — the
    first page is the cover and the last content page is the wrap-up — so a deck
    cannot come out as N identical pages just because the model left the field
    empty. Relying on the prompt alone would be a request, not a guarantee.
    """
    raw = normalize_layout(spec.get("layout"))
    if raw in CANONICAL_LAYOUTS:
        return raw
    if index == 0:
        return "cover"
    if index == total - 1 and total >= 4:
        return "summary"
    return DEFAULT_LAYOUT


def _decorate_slide(slide, layout: str) -> None:
    """程序化图形层：没有上传素材时，页面也要有视觉。

    全部用形状画（圆形、色条、三角），不依赖任何图片文件。装饰必须在文字
    之前绘制——python-pptx 后加的形状在上层，先画才不会盖住正文。

    以前每页都是"标题 + 一个浅底面板 + 一列圆点"，整册一个样，这就是
    "没有图形、没有设计感"的直接来源。
    """
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches

    def shape(kind, left: float, top: float, width: float, height: float, color: str):
        item = slide.shapes.add_shape(
            kind, Inches(left), Inches(top), Inches(width), Inches(height)
        )
        item.fill.solid()
        item.fill.fore_color.rgb = RGBColor.from_string(color)
        item.line.fill.background()
        item.shadow.inherit = False
        return item

    if layout == "cover":
        # 右上角叠放的几何圆组 + 左下暖色点：封面不再是"空白页 + 大字"
        shape(MSO_SHAPE.OVAL, 9.95, 0.30, 1.60, 1.60, COLOR_DECOR)
        shape(MSO_SHAPE.OVAL, 11.15, 1.35, 1.05, 1.05, COLOR_DECOR_WARM)
        shape(MSO_SHAPE.OVAL, 10.55, 2.55, 0.62, 0.62, COLOR_DECOR)
        shape(MSO_SHAPE.OVAL, 0.50, 6.05, 0.40, 0.40, COLOR_DECOR_WARM)
    elif layout == "section":
        # 右侧斜切色块：章节页的底色分区，与正文页一眼可分
        shape(MSO_SHAPE.RIGHT_TRIANGLE, 9.10, 0, SLIDE_WIDTH_IN - 9.10, SLIDE_HEIGHT_IN, COLOR_DECOR)
    elif layout in ("bullets", "agenda", "summary", "steps"):
        # 正文页：内容区左侧主色细竖条 + 页脚色条，打破"一大块浅底"
        shape(
            MSO_SHAPE.RECTANGLE,
            CONTENT_LEFT_IN,
            CONTENT_TOP_IN + 0.16,
            0.05,
            CONTENT_HEIGHT_IN - 0.32,
            COLOR_PRIMARY,
        )
        shape(MSO_SHAPE.RECTANGLE, 0, SLIDE_HEIGHT_IN - 0.06, SLIDE_WIDTH_IN, 0.06, COLOR_PRIMARY)
    elif layout == "compare":
        # 对比页：左右两栏顶部各一道色条（冷暖对照），视觉上先分清两派
        shape(MSO_SHAPE.RECTANGLE, CONTENT_LEFT_IN, CONTENT_TOP_IN - 0.10, 5.28, 0.05, COLOR_PRIMARY)
        shape(MSO_SHAPE.RECTANGLE, 6.55, CONTENT_TOP_IN - 0.10, 5.28, 0.05, COLOR_WARM)
    elif layout == "metric":
        # 度量页：为数值条铺一条底槽（数值条本身在 _render_metric 里按值画）
        shape(MSO_SHAPE.RECTANGLE, CONTENT_LEFT_IN, 6.46, 11.30, 0.10, COLOR_TRACK)


def _short_title(text: str, limit: int = 12) -> str:
    """页眉/页脚用的短课程名。

    蓝图标题往往是"理解浮力的产生原因与阿基米德原理，能判断物体的浮沉条件…"
    这种长句，放进页眉会换行三行、把标题挤走。取第一个分句并截断，页眉才是
    "标签"而不是第二行正文。
    """
    head = re.split(r"[：:，,。；;、（(]", str(text or "").strip())[0].strip()
    if len(head) > limit:
        # 截断要看得出来：直接切齐会像被裁掉的错字
        head = head[: limit - 1] + "…"
    return head


def _add_header_band(slide, course_name: str) -> None:
    """顶部粗色条 + 右上角短课程名（参考课件的页眉体系）。

    以前是"左上角小字标题 + 顶部细线"，页眉几乎看不见；参考课件把课程名放在
    右上角、左侧配一道粗色条 —— 投影时一眼就知道在讲哪门课。
    名字必须短（见 `_short_title`），否则会换行成第二段正文、把页面标题挤走。
    """
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches

    # 只占左侧约 60%，右侧留给课程名，形成参考图那种"条 + 名"的横带
    _add_block(slide, CONTENT_LEFT_IN, 0.30, HEADER_BAR_WIDTH_IN, HEADER_BAR_IN, COLOR_PRIMARY)
    label = _short_title(course_name)
    if not label:
        return
    _add_textbox(
        slide,
        Inches(CONTENT_LEFT_IN + HEADER_BAR_WIDTH_IN + 0.35),
        Inches(HEADER_TITLE_TOP_IN),
        Inches(SLIDE_WIDTH_IN - CONTENT_LEFT_IN - (CONTENT_LEFT_IN + HEADER_BAR_WIDTH_IN + 0.35)),
        Inches(0.48),
        label,
        font_size=20,
        bold=True,
        color=COLOR_PRIMARY,
        align=PP_ALIGN.RIGHT,
    )


def _add_footer(slide, course_name: str, number: int, total: int) -> None:
    """三栏页脚：左《课程名》· 中页码 ·（右侧留空，避免造假品牌）。"""
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches

    _add_textbox(
        slide,
        Inches(CONTENT_LEFT_IN),
        Inches(FOOTER_TOP_IN),
        Inches(7.4),
        Inches(0.34),
        f"《{_short_title(course_name, 20)}》" if course_name else "",
        font_size=CHROME_FONT_SIZE,
        color=COLOR_MUTED,
    )
    _add_textbox(
        slide,
        Inches(5.6),
        Inches(FOOTER_TOP_IN),
        Inches(2.2),
        Inches(0.34),
        f"- {number}/{total}页 -",
        font_size=CHROME_FONT_SIZE,
        color=COLOR_MUTED,
        align=PP_ALIGN.CENTER,
    )


def _add_chrome(slide, course_name: str, number: int, total: int) -> None:
    """内容页的完整页眉页脚：色条 + 课程名 + 三栏页脚。"""
    _add_header_band(slide, course_name)
    _add_footer(slide, course_name, number, total)


def _add_sources(slide, refs: list) -> None:
    """Evidence line, rendered only when there is evidence.

    A deck where every page carries 「来源：暂无可用证据」 reads as a defect, so an
    empty reference list draws nothing at all.
    """
    if not refs:
        return
    from pptx.util import Inches

    _add_textbox(
        slide,
        Inches(CONTENT_LEFT_IN),
        Inches(SOURCE_TOP_IN),
        Inches(10.6),
        Inches(0.26),
        _source_line(refs),
        font_size=8.5,
        color=COLOR_MUTED,
    )


def _title_size(text: str, *, width_in: float, height_in: float) -> float:
    """标题字号自适应：先按 29pt 试，放不进标题框就逐级下调。

    以前标题固定 29pt，长标题换行后会顶到导语与正文面板上 —— "字重叠"就是这么来的。
    python-pptx 没有文本测量 API，所以按"一个全角字≈一个字号宽"保守估算。
    """
    available = height_in * 72
    for size in (29.0, 26.0, 23.0, 20.0, 18.0):
        chars_per_line = max(8, int(width_in * 72 / size))
        lines = max(1, ceil(len(str(text)) / chars_per_line))
        if lines * size * 1.25 <= available:
            return size
    return 18.0


def _lead_size(text: str, *, width_in: float, height_in: float) -> float:
    """导语同样自适应：避免换行后压到正文面板。"""
    available = height_in * 72
    for size in (14.0, 13.0, 12.0):
        chars_per_line = max(8, int(width_in * 72 / size))
        lines = max(1, ceil(len(str(text)) / chars_per_line))
        if lines * size * 1.2 <= available:
            return size
    return 12.0


def _add_slide_title(slide, title: str, *, size: float = 29) -> float:
    """Fill the layout's title placeholder and draw its accent rule.

    Returns the y the body may start at.
    """
    from pptx.enum.text import PP_ALIGN

    fitted = _title_size(title, width_in=CONTENT_WIDTH_IN, height_in=TITLE_HEIGHT_IN)
    placeholder = slide.shapes.title
    if placeholder is not None:
        # 左对齐：版式的标题占位符默认居中，居中标题配左对齐正文会散架
        _fill_placeholder(placeholder, title, size=fitted, bold=True, align=PP_ALIGN.LEFT)
    rule_top = TITLE_TOP_IN + TITLE_HEIGHT_IN - 0.07
    _add_block(slide, CONTENT_LEFT_IN, rule_top, 1.15, 0.055, COLOR_PRIMARY)
    return rule_top + 0.36


def _add_bullets(
    slide,
    bullets: list,
    box: tuple[float, float, float, float],
    *,
    size: float = 20,
) -> None:
    """Draw bullets in a free text box, for pages with no body placeholder.

    One frame rather than one box per bullet: a bullet that wraps to a second
    line then flows naturally instead of colliding with the next box.
    """
    from pptx.util import Inches

    left, top, width, height = box
    body = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = body.text_frame
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    _fill_bullet_frame(frame, bullets, size=size)
    return body


def _drop_body_placeholder(slide) -> None:
    """Remove the body placeholder on pages that compose their own layout.

    Leaving it empty would show a "单击此处添加文本" prompt in PowerPoint's edit
    view, and the page would look unfinished to the teacher.
    """
    placeholder = _body_placeholder(slide)
    if placeholder is None:
        return
    element = placeholder._element
    element.getparent().remove(element)


def _image_pixels(path: Any) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as image:
        return image.width, image.height


def _contain_size(image: tuple[int, int], box: tuple[float, float]) -> tuple[float, float]:
    """Largest width/height that fits inside `box` at the image's aspect ratio."""
    image_w, image_h = image
    box_w, box_h = box
    if image_w / image_h > box_w / box_h:
        return box_w, box_w * image_h / image_w
    return box_h * image_w / image_h, box_h


def _cover_crop(image: tuple[int, int], box: tuple[float, float]) -> dict[str, float]:
    """Crop fractions that let an image fill `box` without stretching it."""
    image_w, image_h = image
    box_w, box_h = box
    image_aspect = image_w / image_h
    box_aspect = box_w / box_h
    if image_aspect > box_aspect:
        excess = 1 - box_aspect / image_aspect
        return {"crop_left": excess / 2, "crop_right": excess / 2}
    excess = 1 - image_aspect / box_aspect
    return {"crop_top": excess / 2, "crop_bottom": excess / 2}


def _image_box(placement: str, *, reserve_bottom: float = 0.0) -> tuple[float, float, float, float]:
    """Picture frame for each placement, optionally leaving room for a caption."""
    if placement in {"background", "full"}:
        # 整页大图与背景图都是满版：整页大图以前只占内容区、四周留白，选了"整页"
        # 却看不到整页的图；现在两者都铺满，区别只在蒙层浓淡与文字处理。
        return 0.0, 0.0, SLIDE_WIDTH_IN, SLIDE_HEIGHT_IN
    half = CONTENT_WIDTH_IN / 2
    left, width = CONTENT_LEFT_IN + half, CONTENT_WIDTH_IN - half
    return left, CONTENT_TOP_IN, width, CONTENT_HEIGHT_IN - reserve_bottom


def _add_picture(slide, path: Any, box: tuple[float, float, float, float], *, cover: bool) -> bool:
    """Place one picture in `box` without distorting it.

    Returns False when the file is missing or unreadable. A deleted or moved
    material must never fail the whole export, so the slide simply renders
    without its picture.
    """
    from pptx.util import Inches

    try:
        pixels = _image_pixels(path)
    except Exception:
        logger.warning("Slide image unreadable, rendering without it: %s", path)
        return False

    left, top, width, height = box
    try:
        if cover:
            picture = slide.shapes.add_picture(
                str(path), Inches(left), Inches(top), Inches(width), Inches(height)
            )
            # OOXML stretches the cropped region to the frame, so cropping to the
            # frame's aspect ratio is what keeps a background from distorting.
            for attribute, value in _cover_crop(pixels, (width, height)).items():
                setattr(picture, attribute, value)
        else:
            draw_width, draw_height = _contain_size(pixels, (width, height))
            slide.shapes.add_picture(
                str(path),
                Inches(left + (width - draw_width) / 2),
                Inches(top + (height - draw_height) / 2),
                Inches(draw_width),
                Inches(draw_height),
            )
    except Exception:
        logger.warning("Slide image could not be embedded, rendering without it: %s", path)
        return False
    return True


# 满版图片上的蒙层：顶端最实（标题所在），向下渐隐。纯色蒙层会把任何图片压成
# 一片灰白 —— "图太灰、看不出是什么"就是这么来的；渐变让图片保持可见，同时
# 中上部的文字仍有足够对比度。
SCRIM_TOP_ALPHA = 0.92
SCRIM_MID_ALPHA = 0.80
SCRIM_BOTTOM_ALPHA = 0.28


def _replace_fill_with_gradient(shape, *, top: float, mid: float, bottom: float) -> None:
    """把形状的纯色填充换成"白色 → 透明"的竖向渐变。

    python-pptx 没有渐变 API，所以整块 ``a:gradFill`` 直接注入 XML；并且**插在
    solidFill 原来的位置**而非末尾：spPr 的子元素顺序在 OOXML 里有约束，追加到
    末尾会让 PowerPoint 认为文件需要修复。
    """
    from pptx.oxml import parse_xml
    from pptx.oxml.ns import nsdecls, qn

    sp_pr = shape._element.spPr
    gradient = parse_xml(
        f'<a:gradFill {nsdecls("a")} rotWithShape="1">'
        "<a:gsLst>"
        f'<a:gs pos="0"><a:srgbClr val="FFFFFF">'
        f'<a:alpha val="{int(top * 100000)}"/></a:srgbClr></a:gs>'
        f'<a:gs pos="62000"><a:srgbClr val="FFFFFF">'
        f'<a:alpha val="{int(mid * 100000)}"/></a:srgbClr></a:gs>'
        f'<a:gs pos="100000"><a:srgbClr val="FFFFFF">'
        f'<a:alpha val="{int(bottom * 100000)}"/></a:srgbClr></a:gs>'
        "</a:gsLst>"
        '<a:lin ang="5400000" scaled="0"/>'
        "</a:gradFill>"
    )
    solid = sp_pr.find(qn("a:solidFill"))
    if solid is not None:
        solid.addprevious(gradient)
        sp_pr.remove(solid)
    else:
        sp_pr.append(gradient)


def _add_scrim(
    slide,
    *,
    top: float = SCRIM_TOP_ALPHA,
    mid: float = SCRIM_MID_ALPHA,
    bottom: float = SCRIM_BOTTOM_ALPHA,
) -> None:
    """Drape a white→transparent gradient over a full-bleed picture.

    python-pptx 既没有渐变也没有透明度 API，所以先按纯色填充建形状（它同时是
    注入失败时的兜底），再把填充换成渐变。任何一步失败导出都照样成功，只是文字
    对比度差一些 —— 一份能打开的课件永远好过一份渲染失败的课件。
    """
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.oxml.ns import qn
    from pptx.util import Inches

    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, 0, 0, Inches(SLIDE_WIDTH_IN), Inches(SLIDE_HEIGHT_IN)
    )
    shape.line.fill.background()
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    try:
        shape.shadow.inherit = False
    except Exception:
        pass
    try:
        srgb = shape._element.spPr.find(qn("a:solidFill")).find(qn("a:srgbClr"))
        srgb.append(srgb.makeelement(qn("a:alpha"), {"val": str(int(mid * 100000))}))
    except Exception:
        logger.warning("Could not make the background scrim translucent")
        return
    try:
        _replace_fill_with_gradient(shape, top=top, mid=mid, bottom=bottom)
    except Exception:
        logger.warning("Could not turn the background scrim into a gradient; keeping a flat wash")


# ── 母版主题与版式 ──────────────────────────────────────

_THEME_NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
# 主题色槽：占位符、表格、图表都会跟随，老师也能在 PowerPoint 里一键换色。
THEME_COLORS = {
    "dk1": COLOR_INK,
    "lt1": "FFFFFF",
    "dk2": COLOR_BAND,
    "lt2": COLOR_SURFACE,
    "accent1": COLOR_PRIMARY,
    "accent2": COLOR_BAND,
    "accent3": COLOR_ACCENT,
    "hlink": COLOR_PRIMARY,
    "folHlink": COLOR_BAND,
}
# 每个版式里占位符的 16:9 栅格。改的是版式定义本身，所以之后新建的每一页都自动
# 合规，在 PowerPoint 里执行"重设版式"也会回到这些位置。
LAYOUT_GRID: dict[str, dict[str, tuple[float, float, float, float]]] = {
    # 封面：整版主蓝 + 白字居中（参考课件构图）
    "Title Slide": {
        "CENTER_TITLE": (1.20, 2.55, 10.93, 1.20),
        "SUBTITLE": (1.20, 3.80, 10.93, 0.85),
    },
    "Title and Content": {
        "TITLE": (CONTENT_LEFT_IN, TITLE_TOP_IN, CONTENT_WIDTH_IN, TITLE_HEIGHT_IN),
        "OBJECT": TEXT_BOXES["full"],
    },
    "Section Header": {
        "TITLE": (1.15, 2.00, 11.00, 0.95),
        "BODY": (1.15, 3.30, 11.00, 0.80),
    },
    "Title Only": {
        "TITLE": (CONTENT_LEFT_IN, TITLE_TOP_IN, CONTENT_WIDTH_IN, TITLE_HEIGHT_IN),
    },
}
# 我们自己画页脚，母版自带的三类占位符留着只会在 PowerPoint 里变成空框。
DROP_PLACEHOLDERS = {"DATE", "FOOTER", "SLIDE_NUMBER"}
LAYOUT_NAMES = {
    "cover": "Title Slide",
    "section": "Section Header",
    "bullets": "Title and Content",
    "agenda": "Title and Content",
    "summary": "Title and Content",
    "steps": "Title and Content",
    "compare": "Title and Content",
    # 下面这些版式自己排布全部内容，不需要正文占位符，所以直接建在"仅标题"
    # 版式上——比建在"标题和内容"再删掉占位符少一步，也不会留下空框。
    "cards": "Title Only",
    "flow": "Title Only",
    "metric": "Title Only",
    "quote": "Title Only",
}


def _apply_theme(prs) -> None:
    """把母版主题的配色与字体换成这套课件的。

    改主题而不是逐个 run 硬编码：占位符、表格、图表会自动跟随，老师也能在
    PowerPoint 的"主题"里一次换色。字体要改两处——`a:ea` 为空会让 PowerPoint
    回退到脚本兜底，而 `<a:font script="Hans">` 的优先级比 `a:ea` 更高。
    """
    from lxml import etree
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT

    theme_part = prs.slide_masters[0].part.part_related_by(RT.THEME)
    root = etree.fromstring(theme_part.blob)

    scheme = root.find("a:themeElements/a:clrScheme", _THEME_NS)
    if scheme is not None:
        for name, value in THEME_COLORS.items():
            node = scheme.find(f"a:{name}", _THEME_NS)
            if node is None or not len(node):
                continue
            leaf = node[0]
            if leaf.get("lastClr") is not None:
                leaf.set("lastClr", value)
            else:
                leaf.set("val", value)

    fonts = root.find("a:themeElements/a:fontScheme", _THEME_NS)
    if fonts is not None:
        for kind in ("majorFont", "minorFont"):
            font = fonts.find(f"a:{kind}", _THEME_NS)
            if font is None:
                continue
            for tag in ("latin", "ea", "cs"):
                node = font.find(f"a:{tag}", _THEME_NS)
                if node is not None:
                    node.set("typeface", SLIDE_FONT)
            for script in font.findall("a:font", _THEME_NS):
                if script.get("script") in {"Hans", "Hant", "Jpan", "Hang"}:
                    script.set("typeface", SLIDE_FONT)

    theme_part._blob = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def _restyle_layouts(prs) -> None:
    """把用到的版式占位符重排到 16:9 栅格。

    python-pptx 自带模板是 4:3（10 x 7.5 in），占位符都按 4:3 摆位；只把画布拉宽
    会让正文挤在左侧 9 in 里、右边空掉 3.3 in。所以启用命名版式之前必须先改版式
    定义，否则会比手画还难看。
    """
    from pptx.util import Inches

    for layout in prs.slide_layouts:
        grid = LAYOUT_GRID.get(layout.name)
        if grid is None:
            continue
        for placeholder in list(layout.placeholders):
            kind = str(placeholder.placeholder_format.type).split(" ")[0]
            if kind in DROP_PLACEHOLDERS:
                element = placeholder._element
                element.getparent().remove(element)
                continue
            box = grid.get(kind)
            if box is None:
                continue
            placeholder.left, placeholder.top, placeholder.width, placeholder.height = (
                Inches(value) for value in box
            )


def _configure_deck(prs) -> None:
    """把空白演示文稿配置成 16:9 的课件母版。"""
    from pptx.util import Inches

    prs.slide_width = Inches(SLIDE_WIDTH_IN)
    prs.slide_height = Inches(SLIDE_HEIGHT_IN)
    _apply_theme(prs)
    _restyle_layouts(prs)


def _layout_for(prs, layout: str):
    name = LAYOUT_NAMES.get(layout, LAYOUT_NAMES["bullets"])
    for candidate in prs.slide_layouts:
        if candidate.name == name:
            return candidate
    return prs.slide_layouts[6]


def _body_placeholder(slide):
    """The layout's second placeholder (body, object or subtitle)."""
    for placeholder in slide.placeholders:
        if placeholder.placeholder_format.idx == 1:
            return placeholder
    return None


def _bring_placeholders_to_front(slide) -> None:
    """Move placeholders above hand-drawn shapes.

    Placeholders exist the moment a slide is created, so an image, scrim or panel
    added afterwards would otherwise cover the text.
    """
    tree = slide.shapes._spTree
    for placeholder in list(slide.placeholders):
        element = placeholder._element
        tree.remove(element)
        tree.append(element)


# ── 文本与强调 ──────────────────────────────────────────


def _emphasis_parts(text: str) -> list[tuple[str, bool]]:
    """Split ``**重点**`` markup into (text, emphasised) runs."""
    parts: list[tuple[str, bool]] = []
    cursor = 0
    for match in EMPHASIS_PATTERN.finditer(text):
        if match.start() > cursor:
            parts.append((text[cursor : match.start()], False))
        parts.append((match.group(1), True))
        cursor = match.end()
    if cursor < len(text):
        parts.append((text[cursor:], False))
    return parts or [(text, False)]


def _style_run(run, text: str, *, size: float, bold: bool, color: str) -> None:
    from pptx.util import Pt

    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = SLIDE_FONT
    run.font.color.rgb = _color(color)
    _set_cjk_typeface(run.font, SLIDE_FONT)


def _suppress_bullet(paragraph) -> None:
    """Turn off the layout's list bullet so our coloured marker is the marker."""
    from pptx.oxml.ns import qn

    pPr = paragraph._p.get_or_add_pPr()
    for tag in ("a:buChar", "a:buAutoNum", "a:buNone"):
        for node in pPr.findall(qn(tag)):
            pPr.remove(node)
    node = pPr.makeelement(qn("a:buNone"), {})
    def_rpr = pPr.find(qn("a:defRPr"))
    if def_rpr is not None:
        def_rpr.addprevious(node)
    else:
        pPr.append(node)


def _fill_bullet_frame(
    frame,
    bullets: list,
    *,
    size: float = 20,
    space_after: float = 14,
    line_spacing: float = 1.18,
) -> None:
    """Write bullets into any text frame, each led by a coloured marker run.

    ``space_after`` / ``line_spacing`` are tunable so a dense cover can tighten
    the list without changing the standard body-page rhythm.
    """
    from pptx.util import Pt

    frame.word_wrap = True
    frame.clear()
    for position, bullet in enumerate(bullets):
        paragraph = frame.paragraphs[0] if position == 0 else frame.add_paragraph()
        paragraph.level = 0
        paragraph.space_after = Pt(space_after)
        paragraph.line_spacing = line_spacing
        _suppress_bullet(paragraph)
        marker = paragraph.add_run()
        _style_run(marker, "●", size=round(size * 0.6), bold=True, color=COLOR_PRIMARY)
        for index, (text, emphasised) in enumerate(_emphasis_parts(str(bullet))):
            run = paragraph.add_run()
            _style_run(
                run,
                f"  {text}" if index == 0 else text,
                size=size,
                bold=emphasised,
                color=COLOR_PRIMARY if emphasised else COLOR_INK,
            )


def _fill_placeholder(
    placeholder,
    text: str,
    *,
    size: float,
    bold: bool = False,
    color: str = COLOR_INK,
    align=None,
    line_spacing=None,
) -> None:
    from pptx.util import Pt

    frame = placeholder.text_frame
    frame.word_wrap = True
    frame.clear()
    paragraph = frame.paragraphs[0]
    paragraph.text = str(text)
    paragraph.font.size = Pt(size)
    paragraph.font.bold = bold
    paragraph.font.name = SLIDE_FONT
    paragraph.font.color.rgb = _color(color)
    if line_spacing is not None:
        paragraph.line_spacing = line_spacing
    _set_cjk_typeface(paragraph.font, SLIDE_FONT)
    if align is not None:
        paragraph.alignment = align


def _fill_body(slide, bullets: list, *, size: float = 20) -> bool:
    """Fill the layout's body placeholder. Returns False when there is none."""
    placeholder = _body_placeholder(slide)
    if placeholder is None:
        return False
    _fill_bullet_frame(placeholder.text_frame, bullets, size=size)
    return True


def _cover_bullet_style(count: int) -> tuple[float, float]:
    """封面要点字号自适应：条目越多，字号与段后距越紧，尽量把要点留在封面上。

    封面要点是"授课对象/课时/目标"这类要投影给全班的元信息，所以整体比正文下限
    再高一点（15/14/13），只有条目很多时才收紧到 12。
    """
    if count <= 3:
        return 15.0, 14.0
    if count == 4:
        return 14.0, 10.0
    if count == 5:
        return 13.0, 8.0
    return 12.0, 5.0


def _fit_cover_bullets(bullets: list, *, size: float, space_after: float) -> tuple[list, list]:
    """按可用高度估算能上屏的条目，其余交回调用方并入讲稿。

    python-pptx 没有文本测量 API，这里按中文最宽情形估算行数（一个全角字约等于
    一个字号宽），宁可保守，也不让文字压到页脚之外。
    """
    available = (COVER_BULLET_BOTTOM_IN - COVER_BULLET_TOP_IN) * 72
    chars_per_line = max(8, int(COVER_BULLET_WIDTH_IN * 72 / size))
    used = 0.0
    visible: list = []
    for index, bullet in enumerate(bullets):
        lines = max(1, (len(bullet) + chars_per_line - 1) // chars_per_line)
        height = lines * size * 1.2 + space_after
        if visible and used + height > available:
            return visible, bullets[index:]
        used += height
        visible.append(bullet)
    return visible, []


def _render_cover(
    slide,
    spec,
    plan: dict,
    picture: dict,
    source_bullets: list,
    total: int = 1,
) -> list:
    """Title slide: a colour band on the left, title and subtitle on the right.

    A background picture is the one placement that makes sense here, so it is
    drawn first and dimmed; the band and text then sit on top of it.

    Returns the bullets the cover could not show, so the caller can keep them in
    the speaker notes. The cover used to receive no bullets at all: an edit that
    enriched the first page was rendered nowhere and looked like "the export
    ignored my revision".
    """
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches

    if picture["path"] is not None:
        # 封面同样尊重 image.placement：以前这里写死整页背景，于是在封面上调整
        # 右侧图文 / 背景图对导出毫无影响，而预览按 placement 渲染 —— 预览与导出
        # 各画各的，"调整位置"看起来就是坏的。
        #   right       -> 图片只铺右半页，左侧色带保持纯色
        #   full/background -> 整页铺满（封面没有独立内容区，两者视觉一致）
        if str(picture.get("placement") or "background") == "right":
            box = (
                COVER_IMAGE_LEFT_IN,
                0.0,
                SLIDE_WIDTH_IN - COVER_IMAGE_LEFT_IN,
                SLIDE_HEIGHT_IN,
            )
        else:
            box = _image_box("background")
        if _add_picture(slide, picture["path"], box, cover=True):
            # 蒙层铺满整页：色带随后绘制在蒙层之上，所以左侧仍是纯色，
            # 右侧图片被压暗以保证标题与要点可读。
            # 封面左侧色带是纯色，渐变主要作用在右半页：标题区留得住对比度，
            # 图片下半部分透出来。
            _add_scrim(slide, top=0.90, mid=0.82, bottom=0.34)

    if picture["path"] is not None and str(picture.get("placement") or "") == "right":
        # 右侧配图时左半页仍是整版主蓝，保证白字可读
        _add_block(slide, 0, 0, COVER_IMAGE_LEFT_IN, COVER_BAND_HEIGHT_IN, COLOR_PRIMARY)
    else:
        _add_block(slide, 0, 0, SLIDE_WIDTH_IN, COVER_BAND_HEIGHT_IN, COLOR_PRIMARY)

    # 右下角浅蓝大圆 + 左下角水印课程名：参考课件的封面靠这两样东西撑住"分量"，
    # 而不是靠更多文字。
    _add_block(
        slide,
        SLIDE_WIDTH_IN - 3.4,
        COVER_BAND_HEIGHT_IN - 3.2,
        4.6,
        4.6,
        COLOR_PRIMARY_LIGHT,
        oval=True,
    )
    # 水印只放短名、单行、靠右下：整句课名当水印会跨三行，把信息行与页脚一起压住
    watermark = _short_title(_plan_title(plan), 8)
    if watermark:
        _add_textbox(
            slide,
            Inches(7.20),
            Inches(5.10),
            Inches(5.50),
            Inches(0.95),
            watermark,
            font_size=40,
            bold=True,
            color=COLOR_PRIMARY_LIGHT,
            align=PP_ALIGN.RIGHT,
        )

    title = slide.shapes.title
    if title is not None:
        _fill_placeholder(
            title,
            str(spec.get("title") or _plan_title(plan)),
            size=40,
            bold=True,
            color=COLOR_ON_PRIMARY,
            line_spacing=1.15,
        )

    # 副标题：课程的定位/目标，白字居中，压在标题下方
    subtitle = str(spec.get("purpose") or plan.get("teaching_goal") or "").strip()
    if subtitle:
        placeholder = _body_placeholder(slide)
        if placeholder is not None:
            _fill_placeholder(
                placeholder, subtitle, size=16, color=COLOR_ON_PRIMARY, line_spacing=1.35
            )

    # 信息行：授课对象 · 课时 · 封面要点（放得下的部分），白字居中；
    # 装不下的条目仍由调用方并入讲稿，内容不会丢。
    meta = [str(plan.get("target_audience") or "").strip()]
    minutes = plan.get("duration_minutes")
    if minutes:
        meta.append(f"{minutes} 分钟")
    meta_line = " · ".join(line for line in meta if line)
    if meta_line:
        _add_textbox(
            slide,
            Inches(1.20),
            Inches(4.78),
            Inches(10.93),
            Inches(0.50),
            meta_line,
            font_size=16,
            color=COLOR_ON_PRIMARY,
            align=PP_ALIGN.CENTER,
        )
    _add_footer(slide, _plan_title(plan), 1, total)
    # 封面要点不铺在封面上：参考课件的封面只有标题与信息行。
    # 条目全部并入讲稿，内容一条不丢。
    return [str(item) for item in source_bullets if str(item).strip()]


def _render_section(slide, spec, picture: dict) -> None:
    """Chapter divider: a tinted page carrying only the section name.

    A picture here becomes a backdrop; the tinted panel is skipped in that case
    because an opaque panel would hide the picture entirely.
    """
    from pptx.util import Inches

    backdrop = False
    if picture["path"] is not None:
        if _add_picture(slide, picture["path"], _image_box("background"), cover=True):
            # 章节页只有一行标题，蒙层可以更透一些，让图片本身当主角。
            _add_scrim(slide, top=0.90, mid=0.84, bottom=0.45)
            backdrop = True
    if not backdrop:
        _add_block(slide, 0, 0, SLIDE_WIDTH_IN, SLIDE_HEIGHT_IN, COLOR_SURFACE)
    _add_block(slide, 0, 3.0, SLIDE_WIDTH_IN, 0.07, COLOR_PRIMARY)
    title = slide.shapes.title
    if title is not None:
        _fill_placeholder(
            title, str(spec.get("title") or ""), size=34, bold=True, color=COLOR_BAND
        )
    purpose = str(spec.get("purpose") or "").strip()
    if purpose:
        placeholder = _body_placeholder(slide)
        if placeholder is not None:
            _fill_placeholder(placeholder, purpose, size=14, color=COLOR_MUTED)


def _render_agenda(slide, spec, items: list) -> list:
    """Numbered overview page: placeholder title, drawn badges for the items.

    Returns the items the page could not show so the caller can keep them in the
    speaker notes. 目录页一行一项，排不下必须说出来：静默丢掉会让教师以为
    "模型没写"，其实是版式装不下。
    """
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches

    _drop_body_placeholder(slide)
    _add_slide_title(slide, str(spec.get("title") or "本课结构"))
    _add_lead_in(slide, str(spec.get("purpose") or ""))
    rows, dropped = _layout_capacity(
        [str(item) for item in items if str(item).strip()], AGENDA_ROW_CAPACITY
    )
    if not rows:
        return dropped

    # 参考课件的目录页：灰色"书签"块（右端带尖角）里放大号蓝色数字，右侧接条目名。
    # 这是整份模板最有辨识度的一处，原先的圆形小徽标没有这种版式感。
    _add_panel(slide, PANEL_BOXES["full"])
    block_width = 1.30
    block_height = 0.70
    # 整组水平居中：块 + 间隙 + 条目名，左右留白对称才像一页设计过的目录
    group_width = 5.60
    group_left = (SLIDE_WIDTH_IN - group_width) / 2 - 0.60
    name_left = group_left + block_width + 0.40
    name_width = group_left + group_width - name_left
    # 从导语下方开始：以前从内容区顶部算，第一块会压在导语上
    top_limit = LEAD_TOP_IN + LEAD_HEIGHT_IN + 0.30
    bottom_limit = CONTENT_TOP_IN + CONTENT_HEIGHT_IN - 0.25
    gap = min(0.98, (bottom_limit - top_limit) / len(rows))
    start_top = top_limit + ((bottom_limit - top_limit) - gap * len(rows)) / 2

    for position, item in enumerate(rows, start=1):
        row_top = start_top + (position - 1) * gap
        block = _add_block(
            slide,
            group_left,
            row_top,
            block_width,
            block_height,
            COLOR_TRACK,  # 比内容区浅灰深一档，块才"浮"得出来
            kind=MSO_SHAPE.PENTAGON,
        )
        _set_shape_text(block, str(position), size=26, bold=True, color=COLOR_PRIMARY)
        _add_textbox(
            slide,
            Inches(name_left),
            Inches(row_top + 0.06),
            Inches(name_width),
            Inches(0.58),
            str(item),
            font_size=24,
            bold=True,
            color=COLOR_PRIMARY,
        )
    return dropped


def _render_summary(slide, spec, bullets: list) -> None:
    """Wrap-up page: the points sit on a tinted panel so it reads as a close."""
    from pptx.util import Inches

    _add_panel(slide, PANEL_BOXES["full"])
    title = slide.shapes.title
    if title is not None:
        _fill_placeholder(
            title, str(spec.get("title") or "本课小结"), size=28, bold=True, color=COLOR_BAND
        )
    _add_lead_in(slide, str(spec.get("purpose") or ""))
    box = TEXT_BOXES["full"]
    # 小结页同样按内容量取字号：要点里出现"整节课的收束"时往往比普通页更长。
    size = _fit_body_size(bullets, width_in=box[2], height_in=box[3], base=18.0)
    if not _fill_body(slide, bullets, size=size):
        _add_bullets(slide, bullets, box, size=size)


def _add_emphasis_text(
    slide,
    text: str,
    box: tuple[float, float, float, float],
    *,
    size: float,
    color: str = COLOR_INK,
):
    """A single paragraph that honours `**重点**` markup, without a bullet marker."""
    from pptx.util import Inches

    left, top, width, height = box
    shape = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = shape.text_frame
    frame.word_wrap = True
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    frame.clear()
    paragraph = frame.paragraphs[0]
    paragraph.line_spacing = 1.2
    for run_text, emphasised in _emphasis_parts(str(text)):
        run = paragraph.add_run()
        _style_run(
            run,
            run_text,
            size=size,
            bold=emphasised,
            color=COLOR_PRIMARY if emphasised else color,
        )
    return shape


def _render_steps(slide, spec, bullets: list) -> list:
    """Numbered procedure page: one badge and one row per step.

    Used where the page describes an order of operations — a plain bullet list
    hides the sequence, which on those pages *is* the content.
    """
    _drop_body_placeholder(slide)
    _add_slide_title(slide, str(spec.get("title") or "操作步骤"))
    _add_lead_in(slide, str(spec.get("purpose") or ""))

    candidates = [str(item).strip() for item in bullets if str(item).strip()]
    steps, dropped = _layout_capacity(candidates, 5)
    if not steps:
        return dropped
    row_height = 0.82
    _add_panel(
        slide,
        (
            PANEL_BOXES["full"][0],
            PANEL_BOXES["full"][1],
            PANEL_BOXES["full"][2],
            min(CONTENT_HEIGHT_IN, row_height * len(steps) + 0.34),
        ),
    )
    for position, step in enumerate(steps, start=1):
        row_top = CONTENT_TOP_IN + 0.17 + (position - 1) * row_height
        badge = _add_block(slide, 1.06, row_top + 0.06, 0.46, 0.46, COLOR_PRIMARY, oval=True)
        _set_shape_text(badge, str(position), size=16, bold=True, color=COLOR_ON_BAND)
        _add_emphasis_text(
            slide,
            step,
            (1.72, row_top, 10.6, row_height - 0.08),
            size=18,
        )
    return dropped


def _render_compare(slide, spec, bullets: list) -> None:
    """Two-column comparison page.

    The split convention (first half left, second half right) is stated in the
    prompt so the model controls it instead of the renderer guessing.
    """
    _drop_body_placeholder(slide)
    _add_slide_title(slide, str(spec.get("title") or "对比"))
    _add_lead_in(slide, str(spec.get("purpose") or ""))

    items = [str(item) for item in bullets if str(item).strip()]
    if not items:
        return
    half = (len(items) + 1) // 2
    columns = [items[:half], items[half:]]
    if not columns[1]:
        # 只给了一组内容时退化成单栏，别让右栏空着
        _add_panel(slide, PANEL_BOXES["full"])
        _add_bullets(slide, columns[0], TEXT_BOXES["full"], size=19)
        return

    width = 5.72
    for index, column in enumerate(columns):
        left = 0.72 + index * (width + 0.46)
        _add_panel(slide, (left, CONTENT_TOP_IN, width, CONTENT_HEIGHT_IN))
        _add_bullets(
            slide,
            column,
            (left + 0.32, CONTENT_TOP_IN + 0.28, width - 0.64, CONTENT_HEIGHT_IN - 0.56),
            size=18,
        )


def _split_card_label(text: str) -> tuple[str, str]:
    """把「小标题：说明」拆成两层，拆不动就把整条当正文。

    只有冒号足够靠前才拆。冒号出现在长句中部时（"光合作用的基本过程：叶绿体
    吸收光能并转化为化学能"），前半是一整个从句而不是标题，拆出来会得到一个
    又长又不像标题的"小标题"，比不拆更难看。
    """
    for colon in ("：", ":"):
        head, separator, tail = text.partition(colon)
        if separator and tail.strip() and 0 < len(head.strip()) <= CARD_LABEL_MAX_CHARS:
            return head.strip(), tail.strip()
    return "", text.strip()


def _layout_capacity(entries: list, limit: int) -> tuple[list, list]:
    """按版面容量切分：装得下的与装不下的。

    这些版式各有形状上的硬上限（卡片 4 张、流程 4 段、度量 3 块），与"模型写了多少
    条"无关。装不下的条目绝不能静默丢掉：调用方会把它们并入讲稿，讲稿里看得见，
    教师也才知道是版式限制而不是内容缺失。
    """
    return entries[:limit], entries[limit:]


def _overflow_note(notes: str, items: list) -> str:
    """把版面放不下的条目并入讲稿，保证内容不因版式而消失。"""
    lines = "\n".join(f"• {strip_emphasis(item)}" for item in items)
    if not lines:
        return notes
    return f"本页版面容纳不下以下条目，已保留在讲稿中：\n{lines}\n\n{notes}".rstrip()


def _expand_slides(slides: list[dict], capacity: int) -> list[dict]:
    """把要点超过一页容量的幻灯片拆成"首页 + 续页"。

    生成端已经不限制每页条数，渲染端如果仍按 max_bullets 截断，教师看到的就是
    "我写了 12 条，导出只剩 5 条"。这里改成分页：每页保持可投影的密度，多出来的
    条目成为续页继续上屏，顺序与语义都不变。
    """
    if capacity < 1:
        return list(slides)
    expanded: list[dict] = []
    for spec in slides:
        bullets = [str(item) for item in (spec.get("bullets") or [])]
        layout = str(spec.get("layout") or DEFAULT_LAYOUT)
        # 封面与章节页不拆：它们的多余条目本来就并进讲稿；拆出来的"课名（续 1）"
        # 会变成一页没有标题意义的内容页，看起来像重复生成。
        if len(bullets) <= capacity or layout in {"cover", "section"}:
            expanded.append(spec)
            continue
        title = str(spec.get("title") or "教学内容")
        for position, start in enumerate(range(0, len(bullets), capacity)):
            clone = dict(spec)
            clone["bullets"] = bullets[start : start + capacity]
            if position:
                # 续页一律退回标准讲解版式：封面、目录、小结、章节页在第二页没有
                # 意义；配图也只跟随首页，否则同一张图会在每一页重复铺满。
                clone["layout"] = DEFAULT_LAYOUT
                clone["title"] = f"{title}（续 {position}）"
                clone["image"] = None
            expanded.append(clone)
    return expanded


def _bullet_height(bullet: str, *, size: float, width_in: float) -> float:
    """一条要点在目标字号下占的高度（与 _fit_body_size 用同一套估算口径）。"""
    chars_per_line = max(8, int(width_in * 72 / size * BODY_WIDTH_SAFETY))
    lines = max(1, ceil(len(str(bullet)) / chars_per_line))
    return lines * size * BODY_LINE_SPACING + BODY_SPACE_AFTER


def _split_pages_by_height(slides: list[dict], images: dict | None = None) -> list[dict]:
    """按"目标字号下的实际高度"再拆一次页。

    条目数没超上限、但单条很长时，渲染端只能一路缩字号（实测出现过整页 12pt）。
    这里先把这类页面拆开，让正文尽量停在 BODY_TARGET_SIZE：
    多一页只是多翻一次，字号太小则是全班都看不清。
    """
    expanded: list[dict] = []
    for spec in slides:
        layout = str(spec.get("layout") or DEFAULT_LAYOUT)
        bullets = [str(item) for item in (spec.get("bullets") or [])]
        if layout not in HEIGHT_AWARE_LAYOUTS or len(bullets) <= 1:
            expanded.append(spec)
            continue
        # 只有"真的有图"的页正文框才更窄（与 render 端同一判断），
        # 否则会把没有图的页也按窄框拆，白白多出续页。
        image_spec = spec.get("image") or {}
        has_image = (images or {}).get(str(image_spec.get("material_id") or "")) is not None
        box = TEXT_BOXES["right"] if has_image else TEXT_BOXES["full"]
        available = box[3] * 72
        heights = [
            _bullet_height(bullet, size=BODY_TARGET_SIZE, width_in=box[2]) for bullet in bullets
        ]
        # 需要几页：按总高度算
        pages_needed = max(1, ceil(sum(heights) / available))
        # 均衡分配：贪心填满会让最后—页只剩一条，整页几乎空白。
        # 这里让每页至少分到 total/pages 条，再受可用高度约束。
        per_page = len(bullets) / pages_needed
        chunks: list[list[str]] = []
        current: list[str] = []
        used = 0.0
        for bullet, height in zip(bullets, heights):
            if current and (used + height > available or len(current) >= ceil(per_page)):
                chunks.append(current)
                current, used = [], 0.0
            current.append(bullet)
            used += height
        chunks.append(current)
        if len(chunks) == 1:
            expanded.append(spec)
            continue
        title = str(spec.get("title") or "教学内容")
        for position, chunk in enumerate(chunks):
            clone = dict(spec)
            clone["bullets"] = chunk
            if position:
                # 续页退回标准讲解版式，并让配图只跟随首页（同 _expand_slides）
                clone["layout"] = DEFAULT_LAYOUT
                clone["title"] = f"{title}（续 {position}）"
                clone["image"] = None
            expanded.append(clone)
    return expanded


def _center_if_sparse(
    frame,
    bullets: list,
    *,
    size: float,
    width_in: float,
    height_in: float,
) -> None:
    """内容明显少于框高时垂直居中。

    拆页之后会出现"一页只剩一条要点"的情况，文字顶在框顶、下方一片空白，
    看起来像漏了内容。居中能让稀疏页也成立。
    """
    from pptx.enum.text import MSO_ANCHOR

    used = sum(
        _bullet_height(str(bullet), size=size, width_in=width_in) for bullet in bullets
    )
    if used < height_in * 72 * 0.6:
        frame.vertical_anchor = MSO_ANCHOR.MIDDLE


def _fit_body_size(
    bullets: list,
    *,
    width_in: float,
    height_in: float,
    base: float = 20.0,
    minimum: float = BODY_MIN_SIZE,
    space_after: float = 14.0,
) -> float:
    """按内容量估一个能排进正文框的字号。

    python-pptx 没有文本测量 API，只能估算。实测按"一个全角字=一个字号宽、行高
    1.18"估会偏乐观：中文禁则处理、项目符号前缀、中英混排都会让实际行数多出
    约一成，结果最后一行压在内容框边框上。所以这里留两道保险——
    每行按 92% 宽度算、行高按 1.32 算 —— 宁可字号小一点，也不要溢出。
    """
    available = height_in * 72
    size = base
    while size > minimum:
        chars_per_line = max(8, int(width_in * 72 / size * BODY_WIDTH_SAFETY))
        used = 0.0
        for bullet in bullets:
            lines = max(1, ceil(len(str(bullet)) / chars_per_line))
            used += lines * size * BODY_LINE_SPACING + space_after
        if used <= available:
            return size
        size -= 1.0
    return minimum


def _render_cards(slide, spec, bullets: list) -> list:
    """并列卡片页：同级、无先后的 3-4 项各占一张卡片。

    要点列表把并列关系压成了"一条条往下读"，学生读到第 3 条时第 1 条已经离开
    视野。卡片让并列一眼可见，这里承载信息的是版面结构本身。
    """
    from pptx.util import Inches

    _drop_body_placeholder(slide)
    _add_slide_title(slide, str(spec.get("title") or "并列要点"))
    _add_lead_in(slide, str(spec.get("purpose") or ""))

    candidates = [str(item).strip() for item in bullets if str(item).strip()]
    entries, dropped = _layout_capacity(candidates, 4)
    if not entries:
        return dropped
    count = len(entries)
    left_origin, _, total_width, _ = PANEL_BOXES["full"]
    width = (total_width - CARD_GAP_IN * (count - 1)) / count
    top = CONTENT_TOP_IN + 0.06
    height = CONTENT_HEIGHT_IN - 0.12

    for position, entry in enumerate(entries):
        left = left_origin + position * (width + CARD_GAP_IN)
        _add_panel(slide, (left, top, width, height), fill=COLOR_PANEL)
        label, body = _split_card_label(entry)
        text_top = top + 0.30
        if label:
            _add_block(slide, left + 0.30, text_top, 0.34, 0.055, COLOR_PRIMARY)
            _add_textbox(
                slide,
                Inches(left + 0.30),
                Inches(text_top + 0.16),
                Inches(width - 0.60),
                Inches(0.46),
                label,
                font_size=17,
                bold=True,
                color=COLOR_BAND,
            )
            text_top += 0.78
        _add_emphasis_text(
            slide,
            body,
            (left + 0.30, text_top, width - 0.60, top + height - text_top - 0.26),
            size=15,
        )
    return dropped


def _render_flow(slide, spec, bullets: list) -> list:
    """流程／因果页：横向流水块，块与块之间用箭头连起来。

    与 steps 的分工是"谁在做"：steps 写的是要学生执行的指令（祈使句），flow 写
    的是这个过程自己如何发生（陈述句）。两者唯一的可见差别就是这里的箭头，
    顺序由图形承担，读者不必去数编号。
    """
    from pptx.enum.shapes import MSO_SHAPE

    _drop_body_placeholder(slide)
    _add_slide_title(slide, str(spec.get("title") or "过程"))
    _add_lead_in(slide, str(spec.get("purpose") or ""))

    candidates = [str(item).strip() for item in bullets if str(item).strip()]
    stages, dropped = _layout_capacity(candidates, 4)
    if not stages:
        return dropped
    count = len(stages)
    left_origin, _, total_width, _ = PANEL_BOXES["full"]
    width = (total_width - FLOW_GAP_IN * (count - 1)) / count
    top = CONTENT_TOP_IN + 0.34
    height = CONTENT_HEIGHT_IN - 0.68

    for position, stage in enumerate(stages):
        left = left_origin + position * (width + FLOW_GAP_IN)
        _add_panel(slide, (left, top, width, height), fill=COLOR_PANEL)
        _add_emphasis_text(
            slide,
            stage,
            (left + 0.26, top + 0.28, width - 0.52, height - 0.56),
            size=15,
        )
        if position < count - 1:
            _add_block(
                slide,
                left + width + 0.16,
                top + height / 2 - 0.11,
                FLOW_GAP_IN - 0.32,
                0.22,
                COLOR_PRIMARY,
                kind=MSO_SHAPE.RIGHT_ARROW,
            )
    return dropped


def _metric_reference(entries: list) -> float:
    """数值条的参照上限：取本页最大数字，画出来才与同页其他数字可比。"""
    reference = 0.0
    for entry in entries:
        match = METRIC_PATTERN.match(str(entry).strip())
        if not match:
            continue
        try:
            reference = max(reference, float(match.group(1).replace(",", "")))
        except ValueError:
            continue
    return reference or 1.0


def _render_metric(slide, spec, bullets: list) -> list:
    """数据度量页：值得放大的数字单独成为视觉主体。

    数字埋在句子中间时，投影出去学生抓不到。这里只认"开头就是数字"的条目；
    认不出的条目按普通短句排——这是识别失败时唯一安全的降级，版式不该因为
    文本形式不配合就渲染失败或丢内容。
    """
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches

    _drop_body_placeholder(slide)
    _add_slide_title(slide, str(spec.get("title") or "关键数据"))
    _add_lead_in(slide, str(spec.get("purpose") or ""))

    candidates = [str(item).strip() for item in bullets if str(item).strip()]
    entries, dropped = _layout_capacity(candidates, 3)
    if not entries:
        return dropped
    count = len(entries)
    left_origin, _, total_width, _ = PANEL_BOXES["full"]
    width = (total_width - METRIC_GAP_IN * (count - 1)) / count
    top = CONTENT_TOP_IN + 0.10
    height = CONTENT_HEIGHT_IN - 0.20

    for position, entry in enumerate(entries):
        left = left_origin + position * (width + METRIC_GAP_IN)
        _add_panel(slide, (left, top, width, height), fill=COLOR_PANEL)
        match = METRIC_PATTERN.match(entry)
        figure = match.group(1).strip() if match else ""
        label = entry[match.end() :].lstrip("：:，,、-— ").strip() if match else entry
        if not figure or not label:
            _add_emphasis_text(
                slide,
                entry,
                (left + 0.26, top + 0.34, width - 0.52, height - 0.68),
                size=16,
            )
            continue
        _add_textbox(
            slide,
            Inches(left + 0.24),
            Inches(top + 0.62),
            Inches(width - 0.48),
            Inches(1.30),
            figure,
            font_size=44,
            bold=True,
            color=COLOR_PRIMARY,
            align=PP_ALIGN.CENTER,
        )
        _add_emphasis_text(
            slide,
            label,
            (left + 0.26, top + 2.06, width - 0.52, height - 2.30),
            size=14,
            color=COLOR_MUTED,
        )
        # 数值条：把数字变成"看得见的量"。以本页最大值为满槽，
        # 度量页因此不只是一排大数字，还有一层图形。
        try:
            value = float(figure.replace(",", ""))
        except ValueError:
            continue
        if value <= 0:
            continue
        bar_width = 11.30 / max(count, 1) - 0.24
        bar_left = CONTENT_LEFT_IN + position * (11.30 / max(count, 1)) + 0.12
        _add_block(slide, bar_left, 6.46, bar_width * min(value / _metric_reference(entries), 1.0), 0.10, COLOR_PRIMARY)
    return dropped


def _add_emphasis_paragraphs(
    slide,
    paragraphs: list,
    box: tuple[float, float, float, float],
    *,
    size: float,
    color: str = COLOR_INK,
):
    """一段一段地排带强调标记的正文，每段独立成段。

    用 paragraph 而不是往 run 里塞换行符：python-pptx 的 run 文本不把 ``\\n``
    当换行，塞进去只会得到一个坏掉的行。
    """
    from pptx.util import Inches, Pt

    left, top, width, height = box
    shape = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = shape.text_frame
    frame.word_wrap = True
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    frame.clear()
    for position, text in enumerate(paragraphs):
        paragraph = frame.paragraphs[0] if position == 0 else frame.add_paragraph()
        paragraph.line_spacing = 1.34
        paragraph.space_after = Pt(10)
        for run_text, emphasised in _emphasis_parts(str(text)):
            run = paragraph.add_run()
            _style_run(
                run,
                run_text,
                size=size,
                bold=emphasised,
                color=COLOR_PRIMARY if emphasised else color,
            )
    return shape


def _render_quote(slide, spec, bullets: list) -> None:
    """引用页：左侧主色竖条标出引文边界，出处单独排在右下。

    引文在版面上必须脱离"要点列表"的地位，否则原文和教师自己的话看起来一样，
    学生分不清哪句是要逐字记住的。出处约定为以破折号开头的最后一条。
    """
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches

    _drop_body_placeholder(slide)
    _add_slide_title(slide, str(spec.get("title") or "原文"))
    _add_lead_in(slide, str(spec.get("purpose") or ""))

    entries = [str(item).strip() for item in bullets if str(item).strip()]
    if not entries:
        return
    attribution = ""
    if len(entries) > 1 and entries[-1].startswith(ATTRIBUTION_PREFIXES):
        attribution = entries[-1].lstrip("—-").strip()
        entries = entries[:-1]

    left_origin, _, total_width, _ = PANEL_BOXES["full"]
    left = left_origin + 0.24
    width = total_width - 0.48
    body_top = CONTENT_TOP_IN + 0.24
    body_height = CONTENT_HEIGHT_IN - (1.06 if attribution else 0.48)
    _add_block(slide, left, body_top, QUOTE_BAR_W_IN, body_height, COLOR_PRIMARY)
    _add_emphasis_paragraphs(
        slide,
        entries,
        (left + 0.46, body_top, width - 0.46, body_height),
        size=20,
        color=COLOR_BAND,
    )
    if attribution:
        _add_textbox(
            slide,
            Inches(left + 0.46),
            Inches(body_top + body_height + 0.20),
            Inches(width - 0.46),
            Inches(0.40),
            f"— {attribution}",
            font_size=13,
            color=COLOR_MUTED,
            align=PP_ALIGN.RIGHT,
        )


def _render_body(slide, spec, bullets: list, picture: dict) -> bool:
    """Standard content page.

    Returns whether a full-bleed picture was embedded, so the caller can finish
    the speaker notes without re-deriving what happened here.
    """
    from pptx.util import Inches

    placement = picture["placement"]
    path = picture["path"]
    # 满版图（整页大图 / 背景图）必须最先画，蒙层与文字才有东西可叠；要点直接压在
    # 渐变上，不再被赶进讲稿 —— 那会让投影出去的一页只剩一张图。
    full_bleed = placement in {"background", "full"}

    background_embedded = False
    if path is not None and full_bleed:
        background_embedded = _add_picture(slide, path, _image_box(placement), cover=True)
        if background_embedded:
            _add_scrim(slide)

    body_top = _add_slide_title(slide, str(spec.get("title") or "教学内容"))
    _add_lead_in(slide, str(spec.get("purpose") or ""))

    embedded = False
    if path is not None and not full_bleed:
        box = _image_box(
            placement,
            reserve_bottom=CAPTION_HEIGHT_IN if picture["caption_below"] else 0.0,
        )
        embedded = _add_picture(slide, path, box, cover=False)
        if embedded and picture["caption_below"]:
            _add_textbox(
                slide,
                Inches(box[0]),
                Inches(CONTENT_TOP_IN + CONTENT_HEIGHT_IN - CAPTION_HEIGHT_IN),
                Inches(box[2]),
                Inches(CAPTION_HEIGHT_IN),
                picture["caption"],
                font_size=10,
                color=COLOR_MUTED,
            )

    # 满版页把整页交给图片，要点就叠在渐变蒙层上（全宽文本框）；图片缺失时
    # embedded 为 False，仍按常规要点版式渲染，避免内容凭空消失。
    left, top, width, height = TEXT_BOXES[placement]
    # 密度放开之后字号必须跟着内容量走：固定 20pt 装 8 条要点会直接压出页面之外。
    body_size = _fit_body_size(bullets, width_in=width, height_in=height)
    body = _body_placeholder(slide)
    if body is not None:
        # 满版页已有渐变蒙层当底，再叠一层面板会把图片糊掉。
        if not full_bleed:
            _add_panel(slide, PANEL_BOXES[placement])
        # 占位符默认占满内容区；右侧图文要让出右半区。哪一页带图是逐页信息，
        # 版式层面无从预知，只能改这一页的占位符几何。
        body.left, body.top = Inches(left), Inches(top)
        body.width, body.height = Inches(width), Inches(height)
        _fill_bullet_frame(body.text_frame, bullets, size=body_size)
        _center_if_sparse(
            body.text_frame, bullets, size=body_size, width_in=width, height_in=height
        )
    else:
        if not full_bleed:
            _add_panel(slide, PANEL_BOXES[placement])
        _add_bullets(
            slide, bullets, (left, max(top, body_top), width, height), size=body_size
        )
    return background_embedded


def generate_pptx(
    plan: dict,
    rag_docs: list | None = None,
    references: list | None = None,
    *,
    output_name: str | None = None,
    images: dict[str, Any] | None = None,
) -> str:
    """Render all SlideSpec entries from a CoursewarePlan into a PPTX.

    Every page is drawn shape by shape on a blank layout, so `layout` drives a
    real visual system instead of a title-size tweak: cover, section divider,
    agenda, summary and the standard bulleted page each get their own
    composition, plus a brand bar, running title, page number and an evidence
    line that only appears when there is evidence.

    `images` maps a slide's `image.material_id` to the file backing it, which
    callers with database access resolve through
    ``materials.resolve_slide_images``. Without it the deck still renders, just
    without pictures.
    """
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    _configure_deck(prs)
    slides = _slides(plan)
    pptx_spec = (plan.get("output_specs") or {}).get("pptx") or {}
    # 每页密度取"声明值"与"可读上限"的较小者，且至少 1 条：声明值描述投影密度，
    # 可读上限保证再密的声明也不会把页面排到出界。
    declared_bullets = int(pptx_spec.get("max_bullets_per_slide") or 5)
    bullet_capacity = max(1, min(declared_bullets, SLIDE_BULLET_CAPACITY))
    narrative_arc = [str(item) for item in pptx_spec.get("narrative_arc") or []]
    visual_direction = str(pptx_spec.get("visual_direction") or "")
    prs.core_properties.subject = " → ".join(narrative_arc)
    prs.core_properties.category = visual_direction
    plan_title = _plan_title(plan)
    # 装不下的页在这里就拆开：续页与首页一视同仁，页码、页眉都按"真正会投影的页"
    # 计算，而不是按模型写下的页数计算。
    slides = _expand_slides(slides, bullet_capacity)
    # 再按"目标字号下的高度"拆一次：宁可多一页，也不把正文压到 12pt
    slides = _split_pages_by_height(slides, images)
    total = len(slides)

    for index, spec in enumerate(slides):
        # 版式必须在建页之前定下来：占位符是创建幻灯片时按版式生成的。
        layout = _slide_layout(spec, index, total)

        image_spec = spec.get("image") or {}
        placement = str(image_spec.get("placement") or "right")
        if placement not in IMAGE_PLACEMENTS:
            placement = "right"
        # 没有可用图片时把正文交回整幅宽度。
        # 以前无论有没有图都按 "right" 排，于是 13.3in 的页面右半幅永远空着，
        # 正文被挤进 4.98in 的窄栏、字号被压到 12pt（投影读不清）。
        if placement == "right" and (images or {}).get(
            str(image_spec.get("material_id") or "")
        ) is None:
            placement = "full"
        caption = strip_emphasis(str(image_spec.get("caption") or "")).strip()
        picture = {
            "placement": placement,
            "caption": caption,
            # 满版图没有"图下方"的位置，图注并入讲稿
            "caption_below": bool(caption) and placement not in {"background", "full"},
            "path": (images or {}).get(str(image_spec.get("material_id") or "")),
        }
        if picture["path"] is not None and layout in TEXT_ONLY_LAYOUTS:
            # 目录/步骤/对比/小结靠文字承载，带配图时退回标准版式，图片不会被
            # 静默丢掉。cover / section 会把配图当背景使用，因此保持原版式。
            layout = DEFAULT_LAYOUT
        slide = prs.slides.add_slide(_layout_for(prs, layout))

        source_bullets = (
            narrative_arc if layout == "agenda" and narrative_arc else spec.get("bullets") or []
        )
        # 不再按 max_bullets 切片：超出的条目要么已经被 _expand_slides 拆成续页，
        # 要么由各版式把"装不下的部分"交回 overflow 并入讲稿。切片是以前唯一
        # 会静默吃掉内容的路径。
        bullets = [str(item) for item in source_bullets]
        background_embedded = False
        # 版式各有容量上限（卡片 4、流程 4、度量 3），放不下的条目在这里收集，
        # 稍后并入讲稿。
        overflow: list = []

        if layout == "cover":
            overflow = _render_cover(slide, spec, plan, picture, bullets, total)
        elif layout == "section":
            _decorate_slide(slide, layout)
            _render_section(slide, spec, picture)
        else:
            # 图形层先画（在文字之下），再画页眉页码与正文
            _decorate_slide(slide, layout)
            _add_chrome(slide, plan_title, index + 1, total)
            _add_sources(slide, spec.get("evidence_refs") or [])
            if layout == "agenda":
                overflow = _render_agenda(slide, spec, bullets)
            elif layout == "steps":
                overflow = _render_steps(slide, spec, bullets)
            elif layout == "flow":
                overflow = _render_flow(slide, spec, bullets)
            elif layout == "cards":
                overflow = _render_cards(slide, spec, bullets)
            elif layout == "compare":
                _render_compare(slide, spec, bullets)
            elif layout == "metric":
                overflow = _render_metric(slide, spec, bullets)
            elif layout == "quote":
                _render_quote(slide, spec, bullets)
            elif layout == "summary":
                _render_summary(slide, spec, bullets)
            else:
                background_embedded = _render_body(slide, spec, bullets, picture)

        notes = str(spec.get("speaker_notes") or "")
        if overflow:
            notes = _overflow_note(notes, overflow)
        if background_embedded and caption:
            notes = f"背景图说明：{caption}\n{notes}"
        if index == 0 and visual_direction:
            notes = f"视觉方向：{visual_direction}\n{notes}"
        if notes:
            slide.notes_slide.notes_text_frame.text = notes
        # 占位符在建页时就已经存在，后画的图片/蒙层/底板会盖住它，最后提到最前。
        _bring_placeholders_to_front(slide)

    output_path = settings.output_dir / (output_name or f"ppt_{uuid.uuid4().hex[:24]}.pptx")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    logger.info("Generated PPT: %s", output_path)
    return str(output_path)


def _slide_image_entries(plan: dict, images: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Deck pictures that can actually be read from disk, in slide order.

    Shared by the lesson document and the printable handout, which both list the
    deck's pictures as an appendix. An entry whose file has vanished is left out,
    so a deleted material never breaks those documents.
    """
    entries: list[dict[str, Any]] = []
    for spec in _slides(plan):
        image_spec = spec.get("image") or {}
        path = (images or {}).get(str(image_spec.get("material_id") or ""))
        if path is None:
            continue
        try:
            _image_pixels(path)
        except Exception:
            logger.warning("Slide image unreadable, leaving it out of the appendix: %s", path)
            continue
        entries.append(
            {
                "order": spec.get("order"),
                "title": str(spec.get("title") or "教学内容"),
                "caption": str(image_spec.get("caption") or ""),
                "path": path,
            }
        )
    return entries


def generate_pdf(
    plan: dict,
    rag_docs: list | None = None,
    references: list | None = None,
    *,
    output_name: str | None = None,
    images: dict[str, Any] | None = None,
) -> str:
    """Render a printable lesson document as a real PDF.

    `images` maps a slide's `image.material_id` to its file; the deck's pictures
    are appended as an appendix so a printed handout carries them too.
    """
    import fitz

    document = fitz.open()
    sections = plan.get("lesson_sections") or []
    pdf_spec = (plan.get("output_specs") or {}).get("pdf") or {}
    pages = [
        {
            "heading": _plan_title(plan),
            "body": [
                f"授课对象：{plan.get('target_audience', '')}",
                f"课时：{plan.get('duration_minutes', 45)} 分钟",
                f"教学目标：{plan.get('teaching_goal', '')}",
                f"教学重点：{plan.get('teaching_focus', '')}",
                f"教学难点：{plan.get('teaching_difficulties', '')}",
                f"课程摘要：{pdf_spec.get('printable_summary', '')}",
            ],
        }
    ]
    for section in sections:
        pages.append(
            {
                "heading": (
                    f"{section.get('order', 1)}. {section.get('title', '教学环节')}"
                    f"（{section.get('duration_minutes', 1)} 分钟）"
                ),
                "body": [
                    f"目标：{section.get('objective', '')}",
                    "教师活动：" + "；".join(section.get("teacher_actions") or []),
                    "学生活动：" + "；".join(section.get("student_actions") or []),
                    f"评价：{section.get('assessment', '')}",
                    *(
                        [_source_line(section.get("evidence_refs") or [])]
                        if pdf_spec.get("include_sources", True)
                        else []
                    ),
                ],
            }
        )

    checklist = [str(item) for item in pdf_spec.get("assessment_checklist") or []]
    if checklist:
        pages.append(
            {
                "heading": "学习评价清单",
                "body": [f"□ {item}" for item in checklist],
            }
        )

    image_entries = _slide_image_entries(plan, images)
    total_pages = len(pages) + len(image_entries)

    for page_number, content in enumerate(pages, start=1):
        page = document.new_page(width=595, height=842)
        body = "".join(f"<p>{html_lib.escape(str(line))}</p>" for line in content["body"])
        markup = f"""
        <style>
          body {{ font-family: sans-serif; color: #172033; line-height: 1.65; }}
          h1 {{ color: #123f7a; font-size: 24px; }}
          p {{ font-size: 13px; margin: 10px 0; }}
          footer {{ color: #667085; font-size: 9px; margin-top: 28px; }}
        </style>
        <h1>{html_lib.escape(str(content['heading']))}</h1>
        {body}
        <footer>第 {page_number} / {total_pages} 页</footer>
        """
        page.insert_htmlbox(fitz.Rect(48, 48, 547, 794), markup)

    for offset, entry in enumerate(image_entries, start=1):
        page_number = len(pages) + offset
        page = document.new_page(width=595, height=842)
        caption = (
            f"<p>图注：{html_lib.escape(entry['caption'])}</p>" if entry["caption"] else ""
        )
        heading = (
            '<style>'
            "body { font-family: sans-serif; color: #172033; }"
            "h1 { color: #123f7a; font-size: 20px; }"
            "p { font-size: 12px; color: #667085; }"
            '</style>'
            f"<h1>附录 · 第 {entry['order']} 页 · {html_lib.escape(entry['title'])}</h1>"
            f"{caption}"
        )
        page.insert_htmlbox(fitz.Rect(48, 48, 547, 140), heading)
        # keep_proportion 保证图片等比缩放填进内容区，不会被拉伸
        page.insert_image(
            fitz.Rect(48, 160, 547, 780),
            filename=str(entry["path"]),
            keep_proportion=True,
        )
        page.insert_htmlbox(
            fitz.Rect(48, 792, 547, 812),
            f'<p style="color:#667085;font-size:9px">第 {page_number} / {total_pages} 页</p>',
        )

    output_path = settings.output_dir / (output_name or f"lesson_{uuid.uuid4().hex[:24]}.pdf")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output_path), garbage=4, deflate=True)
    document.close()
    logger.info("Generated PDF: %s", output_path)
    return str(output_path)


def generate_docx(
    plan: dict,
    rag_docs: list | None = None,
    references: list | None = None,
    *,
    output_name: str | None = None,
    images: dict[str, Any] | None = None,
) -> str:
    """Render LessonPlanSectionSpec entries from a CoursewarePlan into DOCX.

    `images` maps a slide's `image.material_id` to its file; the deck's pictures
    are appended as an appendix so the lesson document carries them too.
    """
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    def set_style_font(style, name: str, size: int, *, bold: bool = False) -> None:
        style.font.name = name
        style.font.size = Pt(size)
        style.font.bold = bold
        rpr = style._element.get_or_add_rPr()
        rfonts = rpr.rFonts
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        rfonts.set(qn("w:eastAsia"), name)

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.8)
    section.bottom_margin = Inches(0.8)
    section.left_margin = Inches(0.9)
    section.right_margin = Inches(0.9)
    set_style_font(doc.styles["Normal"], "Microsoft YaHei", 10)
    doc.styles["Normal"].paragraph_format.space_after = Pt(6)
    doc.styles["Normal"].paragraph_format.line_spacing = 1.15
    set_style_font(doc.styles["Title"], "Microsoft YaHei", 22, bold=True)
    set_style_font(doc.styles["Heading 1"], "Microsoft YaHei", 15, bold=True)
    set_style_font(doc.styles["Heading 2"], "Microsoft YaHei", 12, bold=True)

    title = doc.add_heading(_plan_title(plan), level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(10)
    doc.add_paragraph(
        f"授课对象：{plan.get('target_audience', '')}  |  "
        f"课时：{plan.get('duration_minutes', 45)} 分钟  |  "
        f"配套课件：{len(_slides(plan))} 页"
    )
    doc.add_heading("一、教学目标", level=1)
    doc.add_paragraph(str(plan.get("teaching_goal") or _plan_title(plan)))
    doc.add_heading("二、教学重点与难点", level=1)
    doc.add_paragraph(f"重点：{plan.get('teaching_focus') or '围绕核心知识点建立理解'}")
    doc.add_paragraph(f"难点：{plan.get('teaching_difficulties') or '将知识迁移到新情境'}")

    docx_spec = (plan.get("output_specs") or {}).get("docx") or {}
    doc.add_heading("三、课前准备与分层支持", level=1)
    doc.add_heading("课前准备", level=2)
    for item in docx_spec.get("teacher_preparation") or ["检查课件与课堂材料"]:
        doc.add_paragraph(str(item), style="List Bullet")
    doc.add_heading("分层支持", level=2)
    for item in docx_spec.get("differentiation") or ["根据课堂反馈提供分步提示"]:
        doc.add_paragraph(str(item), style="List Bullet")

    doc.add_heading("四、教学过程", level=1)
    sections = plan.get("lesson_sections") or []
    for section in sections:
        doc.add_heading(
            f"{section.get('order', 1)}. {section.get('title', '教学环节')} "
            f"（{section.get('duration_minutes', 1)} 分钟）",
            level=2,
        )
        doc.add_paragraph(f"目标：{section.get('objective', '')}")
        teacher = doc.add_paragraph()
        teacher.add_run("教师活动：").bold = True
        teacher.add_run("；".join(section.get("teacher_actions") or []))
        student = doc.add_paragraph()
        student.add_run("学生活动：").bold = True
        student.add_run("；".join(section.get("student_actions") or []))
        doc.add_paragraph(f"评价：{section.get('assessment', '')}")
        doc.add_paragraph(_source_line(section.get("evidence_refs") or []))

    doc.add_heading("五、互动练习", level=1)
    for interaction in plan.get("interactions") or []:
        doc.add_heading(str(interaction.get("title", "互动练习")), level=2)
        doc.add_paragraph(str(interaction.get("prompt", "")))
        for item in interaction.get("items") or []:
            doc.add_paragraph(str(item), style="List Bullet")

    doc.add_heading("六、课后任务与教学反思", level=1)
    doc.add_heading("课后任务", level=2)
    doc.add_paragraph(str(docx_spec.get("homework") or "用一个新情境解释本课核心知识。"))
    doc.add_heading("教学反思", level=2)
    for item in docx_spec.get("reflection_prompts") or ["学生在哪个环节暴露了理解偏差？"]:
        doc.add_paragraph(str(item), style="List Bullet")

    doc.add_heading("七、来源", level=1)
    refs = plan.get("evidence_refs") or []
    if refs:
        for ref in refs:
            doc.add_paragraph(_source_label(ref), style="List Bullet")
    else:
        doc.add_paragraph("暂无可用证据")

    entries = _slide_image_entries(plan, images)
    if entries:
        doc.add_heading("八、幻灯片配图", level=1)
        for entry in entries:
            doc.add_paragraph(f"第 {entry['order']} 页 · {entry['title']}")
            try:
                doc.add_picture(str(entry["path"]), width=Inches(5.5))
            except Exception:
                logger.warning("Slide image could not be embedded in the DOCX: %s", entry["path"])
                doc.add_paragraph("（该配图无法写入文档，请检查素材文件）")
            if entry["caption"]:
                doc.add_paragraph(f"图注：{entry['caption']}")

    output_path = settings.output_dir / (output_name or f"doc_{uuid.uuid4().hex[:24]}.docx")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    logger.info("Generated DOCX: %s", output_path)
    return str(output_path)


# ── 互动教具（学生动手探究的仿真） ─────────────────────────────
#
# 模型只写参数与公式；画布、控件、读数、引导步骤全部由这里渲染，公式经
# backend.services.expressions 的白名单校验后翻成具名 JS 函数 —— 不用 eval、
# 不用 new Function（那会要求 CSP 放开 unsafe-eval），产出的页面保持严格 CSP。


def _scene_runtime(
    scene: dict,
    function_name: str,
    functions: list[str],
) -> dict:
    """把 scene 的表达式几何属性编译成具名 JS 函数，属性替换为 {"compute": 名字}。

    与读数公式同一机制：客户端只在 TOOL_COMPUTE 里查函数，页面无需 eval。
    颜色 token 在这里翻成画布色值；其余样式属性（归一化时已钳制）原样透传。
    """
    entities: list[dict] = []
    for entity_index, entity in enumerate(scene.get("entities") or [], start=1):
        if not isinstance(entity, dict):
            continue
        kind = str(entity.get("kind") or "")
        expression_props = SCENE_KIND_EXPRESSION_PROPS.get(kind, ())
        compiled: dict[str, Any] = {"kind": kind}
        for prop in expression_props:
            expression = str(entity.get(prop) or "").strip()
            if not expression:
                continue
            try:
                used = referenced_names(expression)
                js_source = to_javascript(expression)
            except ExpressionError:
                continue
            local_names = sorted(name for name in used if name not in {"pi", "e"})
            destruct = f"const {{{', '.join(local_names)}}} = x;" if local_names else ""
            name = f"{function_name}_e{entity_index}_{prop}"
            functions.append(f'  "{name}": function (x) {{ {destruct} return {js_source}; }},')
            compiled[prop] = {"compute": name}
        for key, value in entity.items():
            if key == "kind" or key in expression_props:
                continue
            if key in ("stroke", "fill"):
                token = str(value or "")
                if token == "none":
                    compiled[key] = "none"
                else:
                    compiled[key] = SCENE_PALETTE.get(token, "")
                continue
            compiled[key] = value
        entities.append(compiled)
    payload: dict[str, Any] = {
        "background": SCENE_PALETTE.get(str(scene.get("background") or ""), "#ffffff"),
        "entities": entities,
    }
    loop = scene.get("loop")
    if isinstance(loop, (int, float)) and not isinstance(loop, bool) and loop > 0:
        payload["loop"] = float(loop)
    return payload


def _tool_runtime(tools: list[dict]) -> tuple[str, str]:
    """生成教具运行时：数据 JSON + 每个读数一个具名 JS 函数。"""
    payload: list[dict] = []
    functions: list[str] = []
    for tool_index, tool in enumerate(tools, start=1):
        tool_id = str(tool.get("tool_id") or f"tool_{tool_index:03d}")
        function_name = re.sub(r"[^A-Za-z0-9_]", "_", f"{tool_id}_{tool_index}")
        outputs = []
        for output_index, output in enumerate(tool.get("outputs") or [], start=1):
            expression = str(output.get("expression") or "").strip()
            if not expression:
                continue
            try:
                used = referenced_names(expression)
                js_source = to_javascript(expression)
            except ExpressionError:
                continue
            # pi / e 在 JS 侧映射成 Math.PI / Math.E，不需要解构
            local_names = sorted(
                name for name in used if name not in {"pi", "e"}
            )
            destruct = f"const {{{', '.join(local_names)}}} = x;" if local_names else ""
            name = f"{function_name}_o{output_index}"
            # 存进注册表而不是声明成全局函数：数据里的 compute 是"键"，
            # 客户端用 TOOL_COMPUTE[key] 取函数，不会出现"字符串不可调用"这类错。
            functions.append(f'  "{name}": function (x) {{ {destruct} return {js_source}; }},')
            outputs.append(
                {
                    "key": str(output.get("key") or f"out{output_index}"),
                    "label": str(output.get("label") or output.get("key") or ""),
                    "unit": str(output.get("unit") or ""),
                    "hint": str(output.get("hint") or ""),
                    "compute": name,
                }
            )
        variables = [
            {
                "key": str(variable.get("key")),
                "label": str(variable.get("label") or variable.get("key")),
                "unit": str(variable.get("unit") or ""),
                "min": float(variable.get("min")),
                "max": float(variable.get("max")),
                "default": float(variable.get("default")),
                "step": float(variable.get("step") or 1),
            }
            for variable in tool.get("variables") or []
        ]
        scene = tool.get("scene") if isinstance(tool.get("scene"), dict) else {}
        if str(tool.get("engine") or "") == "scene" and scene.get("entities"):
            scene = _scene_runtime(scene, function_name, functions)
        payload.append(
            {
                "id": tool_id,
                "engine": str(tool.get("engine") or "curve"),
                "title": str(tool.get("title") or ""),
                "variables": variables,
                "outputs": outputs,
                "constants": {
                    str(key): float(value) for key, value in (tool.get("constants") or {}).items()
                },
                "scene": scene,
            }
        )
    data_json = json.dumps(payload, ensure_ascii=False).translate(
        str.maketrans({"<": "\\u003c", ">": "\\u003e", "&": "\\u0026"})
    )
    registry = "const TOOL_COMPUTE = {\n" + "\n".join(functions) + "\n};"
    return data_json, registry


def _tool_sections_html(tools: list[dict]) -> str:
    """每个教具一块：画布 + 参数滑块 + 实时读数 + 先预测 + 引导步骤 + 原理注释。"""
    if not tools:
        return ""
    blocks: list[str] = ['<section class="tools"><h2 class="section-title">动手探究</h2>']
    for tool in tools:
        tool_id = html_lib.escape(str(tool.get("tool_id") or ""))
        title = html_lib.escape(str(tool.get("title") or "课堂探究工具"))
        goal = html_lib.escape(str(tool.get("goal") or ""))
        controls: list[str] = []
        for variable in tool.get("variables") or []:
            key = html_lib.escape(str(variable.get("key")))
            label = html_lib.escape(str(variable.get("label") or key))
            unit = str(variable.get("unit") or "")
            unit_text = html_lib.escape(f"（{unit}）") if unit else ""
            controls.append(
                '<label class="control">'
                f'<span class="control-label">{label}{unit_text}</span>'
                f'<input type="range" data-tool="{tool_id}" data-var="{key}"'
                f' min="{variable.get("min")}" max="{variable.get("max")}"'
                f' step="{variable.get("step")}" value="{variable.get("default")}">'
                f'<output data-out="{tool_id}:{key}">{variable.get("default")}</output>'
                "</label>"
            )
        readouts: list[str] = []
        for output in tool.get("outputs") or []:
            key = html_lib.escape(str(output.get("key")))
            label = html_lib.escape(str(output.get("label") or key))
            unit = html_lib.escape(str(output.get("unit") or ""))
            hint = html_lib.escape(str(output.get("hint") or ""))
            hint_html = f'<span class="readout-hint">{hint}</span>' if hint else ""
            readouts.append(
                '<div class="readout">'
                f'<span class="readout-label">{label}</span>'
                f'<strong data-readout="{tool_id}:{key}">—</strong>'
                f'<span class="readout-unit">{unit}</span>'
                f"{hint_html}</div>"
            )
        predictions = "".join(
            f"<li>{html_lib.escape(str(prompt))}</li>" for prompt in tool.get("predict_prompts") or []
        )
        predict_block = (
            '<details class="predict" open><summary>先想一想再动手</summary>'
            f"<ul>{predictions}</ul></details>"
            if predictions
            else ""
        )
        steps = "".join(
            f"<li>{html_lib.escape(str(step))}</li>" for step in tool.get("guided_steps") or []
        )
        steps_block = f'<ol class="steps">{steps}</ol>' if steps else ""
        checks = "".join(
            f'<li><details class="check"><summary>展开参考答案</summary>'
            f"<p>{html_lib.escape(str(question))}</p></details></li>"
            for question in tool.get("check_questions") or []
        )
        checks_block = f'<ul class="checks">{checks}</ul>' if checks else ""
        note = html_lib.escape(str(tool.get("model_note") or ""))
        note_block = f'<p class="note">{note}</p>' if note else ""
        scene_bar = ""
        if str(tool.get("engine") or "") == "scene":
            scene_bar = (
                '<div class="scene-bar">'
                f'<button type="button" class="scene-button" data-scene-toggle="{tool_id}">暂停</button>'
                f'<button type="button" class="scene-button" data-scene-reset="{tool_id}">重置</button>'
                "</div>"
            )
        blocks.append(
            f"""
  <article class="tool" id="tool_{tool_id}">
    <h3>{title}</h3>
    <p class="goal">{goal}</p>
    <div class="tool-body">
      <canvas id="canvas_{tool_id}" width="880" height="340" role="img"
              aria-label="{title}的可交互示意图"></canvas>
      <div class="tool-side">
        {scene_bar}
        <div class="controls">{''.join(controls)}</div>
        <div class="readouts">{''.join(readouts)}</div>
      </div>
    </div>
    {predict_block}
    {steps_block}
    {checks_block}
    {note_block}
  </article>"""
        )
    blocks.append("</section>")
    return "\n".join(blocks)


_TOOL_STYLES = """
  .section-title { margin-top: 28px; }
  .tool { margin-top: 18px; padding: 20px; border: 1px solid #dfe6f0; border-radius: 8px; background: #fbfdff; }
  .tool h3 { margin: 0 0 6px; }
  .tool .goal { margin: 0 0 14px; color: #47546b; }
  .tool-body { display: grid; grid-template-columns: minmax(0, 1fr) 260px; gap: 16px; align-items: start; }
  .tool canvas { width: 100%; height: auto; border: 1px solid #dfe6f0; border-radius: 6px; background: #fff; }
  .controls { display: grid; gap: 12px; }
  .control { display: grid; grid-template-columns: minmax(0, 1fr) 56px; gap: 4px 8px; align-items: center; }
  .control-label { grid-column: 1 / -1; color: #47546b; font-size: 13px; }
  .control input[type="range"] { width: 100%; }
  .control output { text-align: right; font-variant-numeric: tabular-nums; }
  .readouts { display: grid; gap: 8px; margin-top: 16px; }
  .readout { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 8px; align-items: baseline; padding: 8px 10px; border-radius: 6px; background: #eef4ff; }
  .readout-label { color: #47546b; font-size: 13px; }
  .readout strong { font-variant-numeric: tabular-nums; }
  .readout-unit { color: #6b778c; font-size: 12px; }
  .readout-hint { grid-column: 1 / -1; color: #6b778c; font-size: 12px; }
  .predict { margin-top: 14px; padding: 12px 14px; border-left: 3px solid #1463ff; background: #f2f7ff; }
  .predict summary { cursor: pointer; font-weight: 700; }
  .predict ul, .checks { margin: 8px 0 0; padding-left: 20px; line-height: 1.7; }
  .steps { margin: 14px 0 0; padding-left: 20px; line-height: 1.8; }
  .checks li { margin: 10px 0; }
  .check summary { cursor: pointer; color: #1463ff; }
  .note { margin: 14px 0 0; padding: 12px 14px; border-radius: 6px; background: #f7f9fc; color: #33415c; line-height: 1.7; }
  .scene-bar { display: flex; gap: 8px; margin-bottom: 12px; }
  .scene-button { padding: 6px 14px; border: 1px solid #cad5e5; border-radius: 6px; background: #fff; color: #33415c; cursor: pointer; font-size: 13px; }
  .scene-button:hover { border-color: #1463ff; color: #1463ff; }
  @media (max-width: 760px) { .tool-body { grid-template-columns: 1fr; } }
"""


def _tool_engine_script() -> str:
    """教具的客户端运行时：读数求值 + flow / curve 两个引擎。

    这里刻意不出现 eval / new Function：公式在服务端已校验并翻成具名函数，
    客户端只调用，页面 CSP 因此可以保持严格。
    """
    return """
const TOOL_DATA = __TOOL_DATA__;
__TOOL_FUNCTIONS__

const toolValueCache = {};
const toolFlowState = {};
const toolSceneState = {};
// 场景图元绘制失败只报一次（动画每帧都在重画，逐帧告警会刷屏）。
const toolSceneWarned = {};

function toolNumber(value) {
  if (typeof value !== 'number' || !isFinite(value)) return '—';
  const magnitude = Math.abs(value);
  if (magnitude !== 0 && (magnitude < 0.01 || magnitude >= 100000)) {
    return value.toExponential(2);
  }
  return String(Math.round(value * 1000) / 1000);
}

function toolReadValues(tool) {
  const values = Object.assign({}, tool.constants || {});
  (tool.variables || []).forEach((variable) => {
    const input = document.querySelector(
      'input[data-tool="' + tool.id + '"][data-var="' + variable.key + '"]',
    );
    values[variable.key] = input ? Number(input.value) : Number(variable.default);
  });
  return values;
}

function toolCall(output, values) {
  const compute = TOOL_COMPUTE[output.compute];
  if (typeof compute !== 'function') return NaN;
  try {
    const value = compute(values);
    return typeof value === 'number' && isFinite(value) ? value : NaN;
  } catch (error) {
    return NaN;
  }
}

function toolComputeOutputs(tool, values) {
  const outputs = {};
  (tool.outputs || []).forEach((output) => {
    outputs[output.key] = toolCall(output, values);
    const node = document.querySelector(
      '[data-readout="' + tool.id + ':' + output.key + '"]',
    );
    if (node) node.textContent = toolNumber(outputs[output.key]);
  });
  return outputs;
}

/* ── curve 引擎：以第一个参数为横轴，画第一个读数随它的变化 ── */
function toolDrawCurve(canvas, tool, values) {
  const context = canvas.getContext('2d');
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);
  const xVariable = (tool.variables || [])[0];
  const yOutput = (tool.outputs || [])[0];
  if (!xVariable || !yOutput) return;
  const padding = { left: 64, right: 24, top: 24, bottom: 46 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const samples = [];
  for (let index = 0; index <= 140; index += 1) {
    const x = xVariable.min + ((xVariable.max - xVariable.min) * index) / 140;
    const probe = Object.assign({}, values);
    probe[xVariable.key] = x;
    samples.push([x, toolCall(yOutput, probe)]);
  }
  const finite = samples.filter((sample) => isFinite(sample[1])).map((sample) => sample[1]);
  if (!finite.length) return;
  let yMin = Math.min(...finite);
  let yMax = Math.max(...finite);
  if (yMax - yMin < 1e-9) {
    yMax += Math.abs(yMax || 1) * 0.1 + 1e-6;
    yMin -= Math.abs(yMin || 1) * 0.1 + 1e-6;
  }
  const yPad = (yMax - yMin) * 0.08;
  yMin -= yPad;
  yMax += yPad;
  const toX = (x) => padding.left + ((x - xVariable.min) / (xVariable.max - xVariable.min || 1)) * plotWidth;
  const toY = (y) => padding.top + plotHeight - ((y - yMin) / (yMax - yMin || 1)) * plotHeight;
  context.strokeStyle = '#cad5e5';
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(padding.left, padding.top);
  context.lineTo(padding.left, padding.top + plotHeight);
  context.lineTo(padding.left + plotWidth, padding.top + plotHeight);
  context.stroke();
  context.fillStyle = '#6b778c';
  context.font = '14px Arial, "Microsoft YaHei", sans-serif';
  context.fillText(xVariable.label + (xVariable.unit ? '(' + xVariable.unit + ')' : ''), padding.left + plotWidth / 2 - 40, height - 12);
  context.save();
  context.translate(18, padding.top + plotHeight / 2 + 40);
  context.rotate(-Math.PI / 2);
  context.fillText(yOutput.label + (yOutput.unit ? '(' + yOutput.unit + ')' : ''), 0, 0);
  context.restore();
  context.beginPath();
  samples.forEach((sample, index) => {
    if (!isFinite(sample[1])) return;
    const x = toX(sample[0]);
    const y = toY(sample[1]);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.strokeStyle = '#1463ff';
  context.lineWidth = 2.5;
  context.stroke();
  const currentY = toolCall(yOutput, values);
  if (isFinite(currentY)) {
    const cx = toX(values[xVariable.key]);
    const cy = toY(currentY);
    context.strokeStyle = '#f0700a';
    context.setLineDash([5, 5]);
    context.beginPath();
    context.moveTo(cx, padding.top + plotHeight);
    context.lineTo(cx, cy);
    context.lineTo(padding.left, cy);
    context.stroke();
    context.setLineDash([]);
    context.fillStyle = '#f0700a';
    context.beginPath();
    context.arc(cx, cy, 6, 0, Math.PI * 2);
    context.fill();
  }
}

/* ── flow 引擎：管道剖面。截面宽度来自前两个参数，粒子速度按连续性 ∝ 1/面积 ── */
function toolFlowGeometry(tool, values) {
  const variables = (tool.variables || []).slice(0, 2);
  const sizes = variables.map((variable) => Math.max(Number(values[variable.key]) || 0, 1e-6));
  const maxSize = Math.max.apply(null, sizes.concat([1e-6]));
  return {
    variables,
    widths: sizes.map((size) => 0.24 + 0.52 * (size / maxSize)),
    speeds: sizes.map((size) => Math.pow(maxSize / size, 2)),
  };
}

function toolDrawFlow(canvas, tool, values, outputs) {
  const context = canvas.getContext('2d');
  const width = canvas.width;
  const height = canvas.height;
  const geometry = toolFlowGeometry(tool, values);
  const splitX = width * 0.52;
  const midY = height / 2;
  const half1 = (geometry.widths[0] || 0.5) * height * 0.42;
  const half2 = (geometry.widths[1] || geometry.widths[0] || 0.5) * height * 0.42;
  context.clearRect(0, 0, width, height);
  context.beginPath();
  context.moveTo(0, midY - half1);
  context.lineTo(splitX, midY - half1);
  context.lineTo(splitX, midY - half2);
  context.lineTo(width, midY - half2);
  context.lineTo(width, midY + half2);
  context.lineTo(splitX, midY + half2);
  context.lineTo(splitX, midY + half1);
  context.lineTo(0, midY + half1);
  context.closePath();
  context.fillStyle = '#e8f1ff';
  context.fill();
  context.strokeStyle = '#8fb4e8';
  context.lineWidth = 2;
  context.stroke();
  const state = toolFlowState[tool.id] || (toolFlowState[tool.id] = { particles: null, last: 0 });
  if (!state.particles) {
    state.particles = [];
    for (let index = 0; index < 34; index += 1) {
      state.particles.push({ x: index / 34, offset: ((index % 3) - 1) * 0.28 });
    }
  }
  const now = performance.now();
  const elapsed = state.last ? Math.min((now - state.last) / 1000, 0.05) : 0;
  state.last = now;
  context.fillStyle = '#1463ff';
  state.particles.forEach((particle) => {
    const firstSection = particle.x < 0.52;
    const speed = firstSection ? geometry.speeds[0] : geometry.speeds[1];
    if (elapsed) {
      particle.x += elapsed * 0.12 * Math.min(speed, 40);
      if (particle.x > 1) particle.x -= 1;
    }
    const half = firstSection ? half1 : half2;
    const x = particle.x * width;
    const y = midY + particle.offset * half;
    const radius = firstSection ? 4 : Math.max(2.4, 4 * Math.sqrt(half2 / half1 || 1));
    context.beginPath();
    context.arc(x, y, radius, 0, Math.PI * 2);
    context.fill();
  });
  context.font = '14px Arial, "Microsoft YaHei", sans-serif';
  context.fillStyle = '#47546b';
  const labels = tool.variables || [];
  if (labels[0]) context.fillText(labels[0].label, 14, 26);
  if (labels[1]) context.fillText(labels[1].label, splitX + 14, 26);
  let readoutY = height - 16;
  (tool.outputs || []).slice(0, 2).forEach((output, index) => {
    const value = outputs[output.key];
    const unit = output.unit ? ' ' + output.unit : '';
    context.fillStyle = index === 0 ? '#1463ff' : '#f0700a';
    context.fillText(output.label + '：' + toolNumber(value) + unit, 14 + index * 220, readoutY);
  });
}

/* ── field 引擎：把场画成热力图，探针点由前两个变量定位 ──
   约定：variables[0] / variables[1] 是探针的两个坐标，outputs[0] 是该点的场值。 */
function toolDrawField(canvas, tool, values, outputs) {
  const context = canvas.getContext('2d');
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);
  const xVariable = (tool.variables || [])[0];
  const yVariable = (tool.variables || [])[1];
  const fieldOutput = (tool.outputs || [])[0];
  if (!xVariable || !yVariable || !fieldOutput) return;
  const padding = { left: 52, right: 24, top: 24, bottom: 40 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const columns = 56;
  const rows = 26;
  const grid = [];
  let min = Infinity;
  let max = -Infinity;
  for (let row = 0; row < rows; row += 1) {
    const line = [];
    for (let column = 0; column < columns; column += 1) {
      const x = xVariable.min + ((xVariable.max - xVariable.min) * column) / (columns - 1);
      const y = yVariable.min + ((yVariable.max - yVariable.min) * row) / (rows - 1);
      const probe = Object.assign({}, values);
      probe[xVariable.key] = x;
      probe[yVariable.key] = y;
      const value = toolCall(fieldOutput, probe);
      line.push(value);
      if (isFinite(value)) {
        min = Math.min(min, value);
        max = Math.max(max, value);
      }
    }
    grid.push(line);
  }
  if (!isFinite(min) || !isFinite(max)) return;
  const span = max - min || 1;
  const cellWidth = plotWidth / columns;
  const cellHeight = plotHeight / rows;
  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      const value = grid[row][column];
      if (!isFinite(value)) continue;
      const ratio = (value - min) / span;
      // 蓝 → 青 → 黄 → 红：冷暖两端各自可辨，不靠明度传达大小
      context.fillStyle = 'hsl(' + Math.round(240 - 240 * ratio) + ', 78%, 58%)';
      context.fillRect(
        padding.left + column * cellWidth,
        padding.top + plotHeight - (row + 1) * cellHeight,
        cellWidth + 0.6,
        cellHeight + 0.6,
      );
    }
  }
  context.strokeStyle = '#8fb4e8';
  context.lineWidth = 1;
  context.strokeRect(padding.left, padding.top, plotWidth, plotHeight);
  const probeX =
    padding.left + ((values[xVariable.key] - xVariable.min) / (xVariable.max - xVariable.min || 1)) * plotWidth;
  const probeY =
    padding.top + plotHeight - ((values[yVariable.key] - yVariable.min) / (yVariable.max - yVariable.min || 1)) * plotHeight;
  context.fillStyle = '#0b1120';
  context.beginPath();
  context.arc(probeX, probeY, 7, 0, Math.PI * 2);
  context.fill();
  context.strokeStyle = '#ffffff';
  context.lineWidth = 2.5;
  context.beginPath();
  context.arc(probeX, probeY, 7, 0, Math.PI * 2);
  context.stroke();
  context.fillStyle = '#47546b';
  context.font = '14px Arial, "Microsoft YaHei", sans-serif';
  context.fillText(xVariable.label, padding.left + plotWidth / 2 - 30, height - 12);
  context.fillText(yVariable.label, 8, padding.top + plotHeight / 2);
  const legend = fieldOutput.label + '：' + toolNumber(grid[Math.floor(rows / 2)][Math.floor(columns / 2)]);
  context.fillStyle = '#6b778c';
  context.fillText('场值范围 ' + toolNumber(min) + ' ~ ' + toolNumber(max), padding.left, 16);
  context.fillText(legend, padding.left + plotWidth - 150, 16);
}

/* ── particles 引擎：粒子速度由第一个读数驱动（越快越扩散） ── */
const toolParticleState = {};

function toolDefaults(tool) {
  const defaults = Object.assign({}, tool.constants || {});
  (tool.variables || []).forEach((variable) => {
    defaults[variable.key] = Number(variable.default);
  });
  return defaults;
}

function toolDrawParticles(canvas, tool, values, outputs) {
  const context = canvas.getContext('2d');
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);
  const driver = (tool.outputs || [])[0];
  const current = driver ? toolCall(driver, values) : NaN;
  const baselineValue = driver ? Math.abs(toolCall(driver, toolDefaults(tool))) : NaN;
  const baseline = isFinite(baselineValue) && baselineValue > 1e-9 ? baselineValue : 1;
  const speed = isFinite(current) ? Math.min(Math.max(Math.abs(current) / baseline, 0.15), 6) : 1;
  const padding = 26;
  const boxWidth = width - padding * 2;
  const boxHeight = height - padding * 2 - 22;
  context.fillStyle = '#f4f8ff';
  context.strokeStyle = '#8fb4e8';
  context.lineWidth = 2;
  context.beginPath();
  context.rect(padding, padding, boxWidth, boxHeight);
  context.fill();
  context.stroke();
  const state =
    toolParticleState[tool.id] ||
    (toolParticleState[tool.id] = { items: null, last: 0, phase: 0 });
  if (!state.items) {
    state.items = [];
    for (let index = 0; index < 90; index += 1) {
      state.items.push({
        x: Math.random(),
        y: Math.random() * 0.6 + 0.2,
        angle: Math.random() * Math.PI * 2,
      });
    }
  }
  const now = performance.now();
  const elapsed = state.last ? Math.min((now - state.last) / 1000, 0.05) : 0;
  state.last = now;
  context.fillStyle = '#1463ff';
  state.items.forEach((particle) => {
    if (elapsed) {
      particle.x += Math.cos(particle.angle) * elapsed * 0.12 * speed;
      particle.y += Math.sin(particle.angle) * elapsed * 0.12 * speed;
      // 撞壁反弹：粒子被"关"在容器里，速度越大分布越均匀
      if (particle.x < 0 || particle.x > 1) {
        particle.angle = Math.PI - particle.angle;
        particle.x = Math.min(Math.max(particle.x, 0), 1);
      }
      if (particle.y < 0 || particle.y > 1) {
        particle.angle = -particle.angle;
        particle.y = Math.min(Math.max(particle.y, 0), 1);
      }
    }
    context.beginPath();
    context.arc(
      padding + particle.x * boxWidth,
      padding + particle.y * boxHeight,
      3.4,
      0,
      Math.PI * 2,
    );
    context.fill();
  });
  context.fillStyle = '#47546b';
  context.font = '14px Arial, "Microsoft YaHei", sans-serif';
  if (driver) {
    context.fillText(
      driver.label + '：' + toolNumber(current) + (driver.unit ? ' ' + driver.unit : '') +
        '（运动快慢约为基准的 ' + speed.toFixed(1) + ' 倍）',
      padding,
      height - 8,
    );
  }
}

/* ── balance 引擎：横梁倾斜由前两个变量的不平衡量决定 ── */
function toolDrawBalance(canvas, tool, values, outputs) {
  const context = canvas.getContext('2d');
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);
  const leftVariable = (tool.variables || [])[0];
  const rightVariable = (tool.variables || [])[1];
  const leftValue = leftVariable ? Number(values[leftVariable.key]) : 0;
  const rightValue = rightVariable ? Number(values[rightVariable.key]) : 0;
  const leftWeight = Math.abs(leftValue);
  const rightWeight = Math.abs(rightValue);
  const total = leftWeight + rightWeight || 1;
  const tilt = Math.min(Math.max((rightWeight - leftWeight) / total, -1), 1) * 0.22;
  const pivotX = width / 2;
  const pivotY = height * 0.42;
  const armLength = Math.min(width * 0.36, 320);
  context.save();
  context.translate(pivotX, pivotY);
  context.rotate(tilt);
  context.fillStyle = '#e8f1ff';
  context.strokeStyle = '#8fb4e8';
  context.lineWidth = 2;
  context.beginPath();
  context.rect(-armLength, -9, armLength * 2, 18);
  context.fill();
  context.stroke();
  context.restore();
  const leftTipY = pivotY + Math.sin(tilt) * armLength;
  const rightTipY = pivotY - Math.sin(tilt) * armLength;
  const leftTipX = pivotX - Math.cos(tilt) * armLength;
  const rightTipX = pivotX + Math.cos(tilt) * armLength;
  context.fillStyle = '#1463ff';
  context.strokeStyle = '#1463ff';
  context.lineWidth = 2;
  // 支点
  context.beginPath();
  context.moveTo(pivotX, pivotY + 46);
  context.lineTo(pivotX - 22, pivotY + 76);
  context.lineTo(pivotX + 22, pivotY + 76);
  context.closePath();
  context.fill();
  // 两端向下的载荷箭头：长度 ∝ 该侧的力
  const drawLoad = (x, y, weight, label, color) => {
    const maxWeight = Math.max(leftWeight, rightWeight, 1e-6);
    const length = 26 + 74 * (weight / maxWeight);
    context.strokeStyle = color;
    context.fillStyle = color;
    context.beginPath();
    context.moveTo(x, y);
    context.lineTo(x, y + length);
    context.stroke();
    context.beginPath();
    context.moveTo(x - 7, y + length);
    context.lineTo(x + 7, y + length);
    context.lineTo(x, y + length + 12);
    context.closePath();
    context.fill();
    context.font = '14px Arial, "Microsoft YaHei", sans-serif';
    context.fillText(label, x - 24, y + length + 32);
  };
  drawLoad(leftTipX, leftTipY + 10, leftWeight, toolNumber(leftValue), '#1463ff');
  drawLoad(rightTipX, rightTipY + 10, rightWeight, toolNumber(rightValue), '#f0700a');
  context.fillStyle = '#47546b';
  context.font = '14px Arial, "Microsoft YaHei", sans-serif';
  if (leftVariable && rightVariable) {
    context.fillText(leftVariable.label, leftTipX - 30, leftTipY - 22);
    context.fillText(rightVariable.label, rightTipX - 30, rightTipY - 22);
  }
  const balanceText =
    Math.abs(tilt) < 0.004 ? '两侧相等，横梁平衡' : tilt > 0 ? '右侧更重，向右倾斜' : '左侧更重，向左倾斜';
  context.fillStyle = '#6b778c';
  context.fillText(balanceText, 14, 22);
  const firstOutput = (tool.outputs || [])[0];
  if (firstOutput) {
    context.fillStyle = '#33415c';
    context.fillText(
      firstOutput.label + '：' + toolNumber(toolCall(firstOutput, values)) +
        (firstOutput.unit ? ' ' + firstOutput.unit : ''),
      14,
      height - 12,
    );
  }
}

/* ── circuit 引擎：电池 + 两个电阻（串联），电流读数取 outputs[0] ── */
function toolDrawCircuit(canvas, tool, values, outputs) {
  const context = canvas.getContext('2d');
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);
  const variables = tool.variables || [];
  const left = variables[0];
  const right = variables[1];
  const leftResistance = left ? Math.abs(Number(values[left.key])) : 0;
  const rightResistance = right ? Math.abs(Number(values[right.key])) : 0;
  const padding = 60;
  const leftX = padding;
  const rightX = width - padding;
  const topY = height * 0.28;
  const bottomY = height * 0.74;
  context.strokeStyle = '#33415c';
  context.lineWidth = 2.5;
  context.beginPath();
  context.moveTo(leftX, bottomY);
  context.lineTo(leftX, topY);
  context.lineTo(rightX, topY);
  context.lineTo(rightX, bottomY);
  context.lineTo(leftX, bottomY);
  context.stroke();
  // 电池（左侧）
  context.fillStyle = '#ffffff';
  context.strokeStyle = '#33415c';
  context.beginPath();
  context.rect(leftX - 16, (topY + bottomY) / 2 - 30, 32, 60);
  context.fill();
  context.stroke();
  context.fillStyle = '#33415c';
  context.font = '14px Arial, "Microsoft YaHei", sans-serif';
  context.fillText('电源', leftX - 22, (topY + bottomY) / 2 + 52);
  // 两个电阻：宽度随阻值增长
  const maxResistance = Math.max(leftResistance, rightResistance, 1e-6);
  const drawResistor = (x, y, resistance, variable, color) => {
    const boxWidth = 60 + 60 * (resistance / maxResistance);
    context.fillStyle = '#eef4ff';
    context.strokeStyle = color;
    context.lineWidth = 2.5;
    context.beginPath();
    context.rect(x - boxWidth / 2, y - 16, boxWidth, 32);
    context.fill();
    context.stroke();
    context.fillStyle = color;
    context.fillText(
      (variable ? variable.label : '电阻') + ' ' + toolNumber(resistance) +
        (variable && variable.unit ? ' ' + variable.unit : ''),
      x - boxWidth / 2,
      y + 36,
    );
  };
  drawResistor(width * 0.4, topY, leftResistance, left, '#1463ff');
  if (right) drawResistor(width * 0.68, topY, rightResistance, right, '#f0700a');
  // 电流读数
  const currentOutput = (tool.outputs || [])[0];
  if (currentOutput) {
    context.fillStyle = '#33415c';
    context.fillText(
      currentOutput.label + '：' + toolNumber(toolCall(currentOutput, values)) +
        (currentOutput.unit ? ' ' + currentOutput.unit : ''),
      leftX,
      bottomY + 40,
    );
  }
  const secondOutput = (tool.outputs || [])[1];
  if (secondOutput) {
    context.fillStyle = '#33415c';
    context.fillText(
      secondOutput.label + '：' + toolNumber(toolCall(secondOutput, values)) +
        (secondOutput.unit ? ' ' + secondOutput.unit : ''),
      leftX,
      bottomY + 62,
    );
  }
}

/* ── scene 引擎：模型按图元目录自由搭建的动态场景。几何属性已在服务端编译成
      TOOL_COMPUTE 里的具名函数（可含场景时钟 t 与 path 采样参数 s），这里逐帧
      求值、绘制，不做任何字符串求值，页面 CSP 因此仍是严格的。 ── */
function toolSceneStateFor(tool) {
  let state = toolSceneState[tool.id];
  if (!state) {
    const loop = Number((tool.scene || {}).loop);
    state = toolSceneState[tool.id] = {
      t: 0,
      playing: true,
      last: 0,
      loop: isFinite(loop) && loop > 0 ? loop : 0,
    };
  }
  return state;
}

function toolSceneTick(tool) {
  const state = toolSceneStateFor(tool);
  const now = performance.now();
  const elapsed = state.last ? Math.min((now - state.last) / 1000, 0.05) : 0;
  state.last = now;
  if (!state.playing || !elapsed) return;
  state.t += elapsed;
  if (state.loop > 0 && state.t > state.loop) state.t %= state.loop;
  else if (state.t > 3600) state.t = 3600;
}

function toolSceneScope(values, outputs, t) {
  const scope = Object.assign({}, values, outputs);
  scope.t = t;
  return scope;
}

function toolSceneValue(prop, scope) {
  if (prop && typeof prop === 'object' && prop.compute) {
    const compute = TOOL_COMPUTE[prop.compute];
    if (typeof compute !== 'function') return NaN;
    try {
      const value = compute(scope);
      return typeof value === 'number' && isFinite(value) ? value : NaN;
    } catch (error) {
      return NaN;
    }
  }
  return typeof prop === 'number' && isFinite(prop) ? prop : NaN;
}

function toolSceneColor(value, fallback) {
  return typeof value === 'string' && value ? value : fallback;
}

function toolSceneRectPath(context, x, y, width, height, radius) {
  const r = Math.max(0, Math.min(radius || 0, width / 2, height / 2));
  context.beginPath();
  if (!r) {
    context.rect(x, y, width, height);
    return;
  }
  context.moveTo(x + r, y);
  context.lineTo(x + width - r, y);
  context.arcTo(x + width, y, x + width, y + r, r);
  context.lineTo(x + width, y + height - r);
  context.arcTo(x + width, y + height, x + width - r, y + height, r);
  context.lineTo(x + r, y + height);
  context.arcTo(x, y + height, x, y + height - r, r);
  context.lineTo(x, y + r);
  context.arcTo(x, y, x + r, y, r);
  context.closePath();
}

function toolSceneEntity(context, tool, entity, scope, outputs) {
  const kind = entity.kind;
  const stroke = toolSceneColor(entity.stroke, '#1463ff');
  const fill = toolSceneColor(entity.fill, 'none');
  const lineWidth = Math.max(0.5, Number(entity.stroke_width) || 2);
  context.save();
  context.setLineDash(Array.isArray(entity.dash) ? entity.dash : []);
  if (kind === 'circle') {
    const cx = toolSceneValue(entity.cx, scope);
    const cy = toolSceneValue(entity.cy, scope);
    const r = toolSceneValue(entity.r, scope);
    if (isFinite(cx) && isFinite(cy) && isFinite(r) && r > 0) {
      context.beginPath();
      context.arc(cx, cy, r, 0, Math.PI * 2);
      if (fill !== 'none') { context.fillStyle = fill; context.fill(); }
      context.strokeStyle = stroke;
      context.lineWidth = lineWidth;
      context.stroke();
    }
  } else if (kind === 'rect') {
    let x = toolSceneValue(entity.x, scope);
    let y = toolSceneValue(entity.y, scope);
    let w = toolSceneValue(entity.w, scope);
    let h = toolSceneValue(entity.h, scope);
    if (isFinite(x) && isFinite(y) && isFinite(w) && isFinite(h) && w !== 0 && h !== 0) {
      if (w < 0) { x += w; w = -w; }
      if (h < 0) { y += h; h = -h; }
      toolSceneRectPath(context, x, y, w, h, Number(entity.radius) || 0);
      if (fill !== 'none') { context.fillStyle = fill; context.fill(); }
      context.strokeStyle = stroke;
      context.lineWidth = lineWidth;
      context.stroke();
    }
  } else if (kind === 'line' || kind === 'arrow') {
    const x1 = toolSceneValue(entity.x1, scope);
    const y1 = toolSceneValue(entity.y1, scope);
    const x2 = toolSceneValue(entity.x2, scope);
    const y2 = toolSceneValue(entity.y2, scope);
    if (isFinite(x1) && isFinite(y1) && isFinite(x2) && isFinite(y2)) {
      context.strokeStyle = stroke;
      context.lineWidth = lineWidth;
      context.beginPath();
      context.moveTo(x1, y1);
      context.lineTo(x2, y2);
      context.stroke();
      if (kind === 'arrow' && (x1 !== x2 || y1 !== y2)) {
        const angle = Math.atan2(y2 - y1, x2 - x1);
        const head = Math.max(4, Number(entity.head) || 12);
        context.beginPath();
        context.moveTo(x2, y2);
        context.lineTo(
          x2 - head * Math.cos(angle - Math.PI / 7),
          y2 - head * Math.sin(angle - Math.PI / 7),
        );
        context.moveTo(x2, y2);
        context.lineTo(
          x2 - head * Math.cos(angle + Math.PI / 7),
          y2 - head * Math.sin(angle + Math.PI / 7),
        );
        context.stroke();
      }
    }
  } else if (kind === 'path') {
    const samples = Math.max(2, Math.min(Number(entity.samples) || 120, 240));
    context.strokeStyle = stroke;
    context.lineWidth = lineWidth;
    context.beginPath();
    let started = false;
    for (let index = 0; index <= samples; index += 1) {
      scope.s = index / samples;
      const px = toolSceneValue(entity.x, scope);
      const py = toolSceneValue(entity.y, scope);
      if (!isFinite(px) || !isFinite(py)) { started = false; continue; }
      if (started) context.lineTo(px, py);
      else { context.moveTo(px, py); started = true; }
    }
    if (fill !== 'none') { context.fillStyle = fill; context.fill(); }
    context.stroke();
  } else if (kind === 'text') {
    const x = toolSceneValue(entity.x, scope);
    const y = toolSceneValue(entity.y, scope);
    if (isFinite(x) && isFinite(y)) {
      const size = Math.max(8, Number(entity.size) || 16);
      context.font = size + 'px Arial, "Microsoft YaHei", sans-serif';
      context.textAlign =
        entity.align === 'center' ? 'center' : entity.align === 'right' ? 'right' : 'left';
      context.textBaseline = 'middle';
      context.fillStyle = fill !== 'none' ? fill : '#33415c';
      context.fillText(String(entity.content || ''), x, y);
    }
  } else if (kind === 'readout') {
    const x = toolSceneValue(entity.x, scope);
    const y = toolSceneValue(entity.y, scope);
    const output = (tool.outputs || []).filter((item) => item.key === entity.output)[0];
    if (isFinite(x) && isFinite(y) && output) {
      const size = Math.max(8, Number(entity.size) || 14);
      const text =
        output.label + ' ' + toolNumber(outputs[entity.output]) +
        (output.unit ? ' ' + output.unit : '');
      context.font = size + 'px Arial, "Microsoft YaHei", sans-serif';
      context.textAlign =
        entity.align === 'center' ? 'center' : entity.align === 'right' ? 'right' : 'left';
      context.textBaseline = 'middle';
      const metrics = context.measureText(text);
      let left = x - 6;
      if (context.textAlign === 'center') left = x - metrics.width / 2 - 6;
      else if (context.textAlign === 'right') left = x - metrics.width - 6;
      context.fillStyle = 'rgba(255, 255, 255, 0.85)';
      context.fillRect(left, y - size * 0.9, metrics.width + 12, size * 1.8);
      context.fillStyle = fill !== 'none' ? fill : '#33415c';
      context.fillText(text, x, y);
    }
  }
  context.restore();
}

function toolDrawScene(canvas, tool, values, outputs) {
  const context = canvas.getContext('2d');
  const scene = tool.scene || {};
  const state = toolSceneStateFor(tool);
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = toolSceneColor(scene.background, '#ffffff');
  context.fillRect(0, 0, canvas.width, canvas.height);
  const scope = toolSceneScope(values, outputs, state.t);
  (scene.entities || []).forEach((entity) => {
    try {
      toolSceneEntity(context, tool, entity, scope, outputs);
    } catch (error) {
      /* 单个元素画不出来不影响整幅场景；但要在控制台留痕，别让整类图元悄悄消失 */
      const mark = tool.id + ':' + (entity.kind || '?');
      if (!toolSceneWarned[mark] && window.console && window.console.warn) {
        toolSceneWarned[mark] = true;
        window.console.warn('场景图元绘制失败：' + mark, error);
      }
    }
  });
}

function toolSceneRedraw(tool) {
  const cached = toolValueCache[tool.id];
  if (cached) toolDraw(tool, cached.values, cached.outputs);
}

function toolSceneBindControls(tool, reduced) {
  if (tool.engine !== 'scene') return;
  const state = toolSceneStateFor(tool);
  if (reduced) state.playing = false;
  const toggle = document.querySelector('[data-scene-toggle="' + tool.id + '"]');
  const reset = document.querySelector('[data-scene-reset="' + tool.id + '"]');
  if (toggle) {
    toggle.textContent = reduced ? '前进 0.5 秒' : state.playing ? '暂停' : '继续';
    toggle.addEventListener('click', () => {
      if (reduced) {
        // 减少动态效果：不自动播放，点一次前进半秒，逐步观察
        state.t += 0.5;
        toolSceneRedraw(tool);
        return;
      }
      state.playing = !state.playing;
      state.last = 0;
      toggle.textContent = state.playing ? '暂停' : '继续';
    });
  }
  if (reset) {
    reset.addEventListener('click', () => {
      state.t = 0;
      state.last = 0;
      toolSceneRedraw(tool);
    });
  }
}

const TOOL_ENGINES = {
  flow: toolDrawFlow,
  curve: toolDrawCurve,
  field: toolDrawField,
  particles: toolDrawParticles,
  balance: toolDrawBalance,
  circuit: toolDrawCircuit,
  scene: toolDrawScene,
};

function toolDraw(tool, values, outputs) {
  const canvas = document.getElementById('canvas_' + tool.id);
  if (!canvas) return;
  const draw = TOOL_ENGINES[tool.engine] || toolDrawCurve;
  draw(canvas, tool, values, outputs);
}

function toolRefresh(tool) {
  const values = toolReadValues(tool);
  (tool.variables || []).forEach((variable) => {
    const node = document.querySelector('[data-out="' + tool.id + ':' + variable.key + '"]');
    if (node) node.textContent = toolNumber(values[variable.key]);
  });
  const outputs = toolComputeOutputs(tool, values);
  toolValueCache[tool.id] = { values, outputs };
  toolDraw(tool, values, outputs);
}

function toolInit() {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  TOOL_DATA.forEach((tool) => {
    (tool.variables || []).forEach((variable) => {
      const input = document.querySelector(
        'input[data-tool="' + tool.id + '"][data-var="' + variable.key + '"]',
      );
      if (input) input.addEventListener('input', () => toolRefresh(tool));
    });
    toolSceneBindControls(tool, reduced);
    toolRefresh(tool);
  });
  if (reduced) return;
  // 只有需要动起来的引擎进动画循环：flow 的粒子、particles 的分子运动与 scene 场景
  const ANIMATED = { flow: true, particles: true, scene: true };
  const animate = () => {
    TOOL_DATA.forEach((tool) => {
      if (!ANIMATED[tool.engine]) return;
      const cached = toolValueCache[tool.id];
      if (!cached) return;
      if (tool.engine === 'scene') {
        // 暂停中的场景不再逐帧重绘（拖滑块会自己触发一次 toolRefresh）
        if (!toolSceneStateFor(tool).playing) return;
        toolSceneTick(tool);
      }
      toolDraw(tool, cached.values, cached.outputs);
    });
    window.requestAnimationFrame(animate);
  };
  window.requestAnimationFrame(animate);
}

if (TOOL_DATA.length) toolInit();
"""


def generate_html(
    plan: dict,
    rag_docs: list | None = None,
    references: list | None = None,
    *,
    output_name: str | None = None,
) -> str:
    """Render a scored, resettable activity from one validated InteractionSpec."""
    title = html_lib.escape(_plan_title(plan))
    interactions = plan.get("interactions") or [{}]
    html_spec = (plan.get("output_specs") or {}).get("html") or {}
    requested_ids = html_spec.get("interaction_ids") or []
    interaction = next(
        (
            item
            for interaction_id in requested_ids
            for item in interactions
            if item.get("interaction_id") == interaction_id
        ),
        interactions[0],
    )
    interaction_title = html_lib.escape(str(interaction.get("title") or "互动练习"))
    prompt = html_lib.escape(str(interaction.get("prompt") or "请完成本课互动练习"))
    interaction_type = str(interaction.get("interaction_type") or "classification")
    interaction_type_label = {
        "matching": "配对",
        "ordering": "排序",
        "classification": "分类",
    }.get(interaction_type, "互动")
    interaction_data = {
        "type": interaction_type,
        "items": [str(item) for item in interaction.get("items") or []],
        "answerGroups": {
            str(group): [str(item) for item in values]
            for group, values in (interaction.get("answer_groups") or {}).items()
        },
        "explanation": str(interaction.get("explanation") or "请对照正确答案梳理本题思路。"),
        "feedbackCorrect": str(
            interaction.get("feedback_correct") or "回答正确，已经掌握本题要点。"
        ),
        "feedbackIncorrect": str(
            interaction.get("feedback_incorrect") or "答案还不完整，请结合解析再试一次。"
        ),
        "score": int(interaction.get("score") or 10),
        "completionMessage": str(
            html_spec.get("completion_message") or "练习完成，请结合解析回顾本课要点。"
        ),
        "allowRetry": bool(html_spec.get("allow_retry", True)),
    }
    data_json = json.dumps(interaction_data, ensure_ascii=False).translate(
        str.maketrans({"<": "\\u003c", ">": "\\u003e", "&": "\\u0026"})
    )
    source_text = html_lib.escape(_source_line(interaction.get("evidence_refs") or []))
    # 互动教具：先让学生动手探究，再落到检测题。没有教具的蓝图（含历史蓝图）
    # 走原来的渲染路径，页面与以前完全一致。
    tools = [item for item in (plan.get("interactive_tools") or []) if isinstance(item, dict)]
    tools_json, tool_functions = _tool_runtime(tools)
    tools_html = _tool_sections_html(tools)
    tool_styles = _TOOL_STYLES if tools else ""
    engine_script = (
        _tool_engine_script()
        .replace("__TOOL_DATA__", tools_json)
        .replace("__TOOL_FUNCTIONS__", tool_functions)
        if tools
        else ""
    )
    detection_heading = '<h2 class="section-title">检测环节</h2>' if tools else ""
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:">
<title>{title} - 互动练习</title>
<style>
  body {{ margin: 0; padding: 32px; background: #f4f7fb; color: #172033; font-family: Arial, "Microsoft YaHei", sans-serif; }}
  main {{ max-width: 760px; margin: 0 auto; background: #fff; padding: 32px; border: 1px solid #dfe6f0; border-radius: 8px; }}
  h1 {{ margin-top: 0; }}
  .item {{ display: block; width: 100%; margin: 10px 0; padding: 14px 16px; text-align: left; border: 1px solid #cad5e5; border-radius: 6px; background: #fff; cursor: pointer; }}
  .item:hover, .item.selected {{ border-color: #1463ff; background: #eef4ff; }}
  .assignment {{ display: grid; grid-template-columns: minmax(0, 1fr) 220px; gap: 12px; align-items: center; margin: 10px 0; }}
  select {{ width: 100%; padding: 10px; border: 1px solid #cad5e5; border-radius: 6px; background: #fff; }}
  .actions {{ display: flex; gap: 10px; margin-top: 20px; }}
  .action {{ padding: 10px 18px; border: 1px solid #1463ff; border-radius: 6px; background: #1463ff; color: #fff; cursor: pointer; }}
  .action.secondary {{ background: #fff; color: #1463ff; }}
  #result {{ min-height: 24px; margin-top: 20px; font-weight: 700; }}
  #explanation {{ display: none; padding: 14px; background: #f7f9fc; border-left: 3px solid #1463ff; line-height: 1.6; }}
  .source {{ margin-top: 24px; color: #6b778c; font-size: 12px; }}
  @media (max-width: 600px) {{ body {{ padding: 12px; }} main {{ padding: 20px; }} .assignment {{ grid-template-columns: 1fr; }} }}
{tool_styles}
</style>
</head>
<body>
<main>
  <h1>{title}</h1>
{tools_html}
  {detection_heading}
  <h2>{interaction_title}</h2>
  <p>{prompt}</p>
  <p class="mode">互动方式：{html_lib.escape(interaction_type_label)}</p>
  <section id="items"></section>
  <div class="actions"><button id="submit" class="action">提交答案</button>{'<button id="reset" class="action secondary">重新作答</button>' if interaction_data['allowRetry'] else ''}</div>
  <p id="result" aria-live="polite"></p>
  <p id="explanation"></p>
  <p class="source">{source_text}</p>
</main>
<script>
{engine_script}
const data = {data_json};
const selected = [];
const assignments = new Map();
const itemRoot = document.querySelector('#items');
const groups = Object.keys(data.answerGroups);

function render() {{
  itemRoot.replaceChildren();
  selected.splice(0);
  assignments.clear();
  data.items.forEach((item, index) => {{
    if (data.type === 'ordering' || data.type === 'quiz') {{
      const button = document.createElement('button');
      button.className = 'item';
      button.type = 'button';
      button.textContent = item;
      button.addEventListener('click', () => {{
        const position = selected.indexOf(index);
        if (position >= 0) selected.splice(position, 1); else selected.push(index);
        button.classList.toggle('selected', selected.includes(index));
        document.querySelector('#result').textContent = data.type === 'ordering'
          ? `当前顺序：${{selected.map((value) => value + 1).join('、')}}`
          : `已选择 ${{selected.length}} 项`;
      }});
      itemRoot.append(button);
      return;
    }}
    const row = document.createElement('label');
    row.className = 'assignment';
    const text = document.createElement('span');
    text.textContent = item;
    const select = document.createElement('select');
    select.innerHTML = '<option value="">请选择分组</option>';
    groups.forEach((group) => {{
      const option = document.createElement('option');
      option.value = group;
      option.textContent = group;
      select.append(option);
    }});
    select.addEventListener('change', () => assignments.set(index, select.value));
    row.append(text, select);
    itemRoot.append(row);
  }});
  document.querySelector('#result').textContent = '';
  document.querySelector('#explanation').style.display = 'none';
}}

function expectedGroup(item) {{
  return groups.find((group) => data.answerGroups[group].includes(item)
    || (item.includes(group) && data.answerGroups[group].some((value) => item.includes(value))));
}}

document.querySelector('#submit').addEventListener('click', () => {{
  let correct = 0;
  let total = data.items.length;
  if (data.type === 'ordering') {{
    const expected = groups.length ? data.answerGroups[groups[0]] : [];
    correct = selected.reduce((count, index, position) => count + (data.items[index] === expected[position] ? 1 : 0), 0);
    total = Math.max(expected.length, 1);
  }} else if (data.type === 'quiz') {{
    const expected = new Set(groups.flatMap((group) => data.answerGroups[group]));
    const chosen = new Set(selected.map((index) => data.items[index]));
    correct = [...expected].filter((item) => chosen.has(item)).length;
    correct -= [...chosen].filter((item) => !expected.has(item)).length;
    correct = Math.max(0, correct);
    total = Math.max(expected.size, 1);
  }} else {{
    data.items.forEach((item, index) => {{ if (assignments.get(index) === expectedGroup(item)) correct += 1; }});
  }}
  const percent = Math.round((correct / Math.max(total, 1)) * 100);
  const passed = correct === total;
  document.querySelector('#result').textContent = `${{passed ? `${{data.feedbackCorrect}} ${{data.completionMessage}}` : data.feedbackIncorrect}} 得分：${{percent}} / 100`;
  const explanation = document.querySelector('#explanation');
  explanation.textContent = `解析：${{data.explanation}}`;
  explanation.style.display = 'block';
}});
const resetButton = document.querySelector('#reset');
if (resetButton) resetButton.addEventListener('click', render);
render();
</script>
</body>
</html>"""
    output_path = settings.output_dir / (
        output_name or f"interactive_{uuid.uuid4().hex[:24]}.html"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("Generated HTML: %s", output_path)
    return str(output_path)
