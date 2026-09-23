"""M4 renderers driven by one CoursewarePlan JSON document."""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
import uuid
from typing import Any

from backend.config import settings
from backend.schemas import (  # noqa: F401 - strip_emphasis re-exported
    CANONICAL_LAYOUTS,
    EMPHASIS_PATTERN,
    normalize_layout,
    strip_emphasis,
)

logger = logging.getLogger(__name__)

# 16:9 canvas shared by the pptx renderer and the frontend slide preview.
SLIDE_WIDTH_IN = 13.333
SLIDE_HEIGHT_IN = 7.5
# Content area between the title band and the source/page footer.
CONTENT_LEFT_IN = 0.65
CONTENT_TOP_IN = 1.90
CONTENT_WIDTH_IN = SLIDE_WIDTH_IN - 2 * CONTENT_LEFT_IN
CONTENT_HEIGHT_IN = 4.30
CAPTION_HEIGHT_IN = 0.4
TITLE_TOP_IN = 0.52
TITLE_HEIGHT_IN = 0.86
LEAD_TOP_IN = 1.44
LEAD_HEIGHT_IN = 0.34
SOURCE_TOP_IN = 6.5
FOOTER_TOP_IN = 6.9
IMAGE_PLACEMENTS = ("right", "full", "background")
# 内容面板：要点装进圆角浅色底板，页面才有"容器"与层次，而不是一行行裸文本。
PANEL_BOXES: dict[str, tuple[float, float, float, float]] = {
    "right": (0.72, CONTENT_TOP_IN, 5.58, CONTENT_HEIGHT_IN),
    "full": (0.72, CONTENT_TOP_IN, 11.90, CONTENT_HEIGHT_IN),
    "background": (0.72, CONTENT_TOP_IN, 11.90, CONTENT_HEIGHT_IN),
}
# 面板内留出内边距之后的正文框。`right` 把另一半让给配图。
TEXT_BOXES: dict[str, tuple[float, float, float, float]] = {
    "right": (1.02, CONTENT_TOP_IN + 0.16, 4.98, CONTENT_HEIGHT_IN - 0.32),
    "full": (1.02, CONTENT_TOP_IN + 0.16, 11.30, CONTENT_HEIGHT_IN - 0.32),
    "background": (1.02, CONTENT_TOP_IN + 0.16, 11.30, CONTENT_HEIGHT_IN - 0.32),
}

# ── 视觉系统 ────────────────────────────────────────────
# 幻灯片从空白版式逐块绘制，所以颜色必须显式给出：主题色会随打开它的模板
# 变化，课程的主色不该被别人的模板改掉。
SLIDE_FONT = "Microsoft YaHei"
COLOR_INK = "0F172A"
COLOR_MUTED = "64748B"
COLOR_PRIMARY = "1463FF"
COLOR_ACCENT = "C2410C"
COLOR_SURFACE = "EEF4FF"
COLOR_LINE = "D3E0F5"
COLOR_PANEL = "F5F8FF"
COLOR_BAND = "0B3FA8"
COLOR_ON_BAND = "FFFFFF"

DEFAULT_LAYOUT = "bullets"
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


def _add_panel(
    slide,
    box: tuple[float, float, float, float],
    *,
    fill: str = COLOR_SURFACE,
    line: str | None = COLOR_LINE,
):
    """A rounded panel. Content sits in a container instead of floating on white,
    which is most of the difference between a document and a slide."""
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches

    left, top, width, height = box
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(left),
        Inches(top),
        Inches(width),
        Inches(height),
    )
    try:
        # 圆角半径按短边比例，避免大面板出现夸张的圆角
        shape.adjustments[0] = 0.045
    except Exception:
        pass
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
        font_size=12,
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


def _add_chrome(slide, plan_title: str, number: int, total: int) -> None:
    """Brand bar, running title and page number shared by every content page."""
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches

    _add_block(slide, 0, 0, SLIDE_WIDTH_IN, 0.085, COLOR_PRIMARY)
    _add_textbox(
        slide,
        Inches(CONTENT_LEFT_IN),
        Inches(FOOTER_TOP_IN),
        Inches(8.4),
        Inches(0.28),
        plan_title,
        font_size=9,
        color=COLOR_MUTED,
    )
    _add_textbox(
        slide,
        Inches(11.35),
        Inches(FOOTER_TOP_IN),
        Inches(1.35),
        Inches(0.28),
        f"{number} / {total}",
        font_size=9,
        color=COLOR_MUTED,
        align=PP_ALIGN.RIGHT,
    )


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


def _add_slide_title(slide, title: str, *, size: float = 29) -> float:
    """Fill the layout's title placeholder and draw its accent rule.

    Returns the y the body may start at.
    """
    placeholder = slide.shapes.title
    if placeholder is not None:
        _fill_placeholder(placeholder, title, size=size, bold=True)
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
    "Title Slide": {
        "CENTER_TITLE": (5.15, 2.30, 7.45, 1.85),
        "SUBTITLE": (5.15, 4.52, 7.45, 1.20),
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
    """封面要点字号自适应：条目越多，字号与段后距越紧，尽量把要点留在封面上。"""
    if count <= 3:
        return 13.0, 14.0
    if count == 4:
        return 12.0, 9.0
    return 11.0, 5.0


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


def _render_cover(slide, spec, plan: dict, picture: dict, source_bullets: list) -> list:
    """Title slide: a colour band on the left, title and subtitle on the right.

    A background picture is the one placement that makes sense here, so it is
    drawn first and dimmed; the band and text then sit on top of it.

    Returns the bullets the cover could not show, so the caller can keep them in
    the speaker notes. The cover used to receive no bullets at all: an edit that
    enriched the first page was rendered nowhere and looked like "the export
    ignored my revision".
    """
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

    _add_block(slide, 0, 0, 4.55, SLIDE_HEIGHT_IN, COLOR_BAND)
    _add_block(slide, 4.55, 0, 0.07, SLIDE_HEIGHT_IN, COLOR_PRIMARY)

    meta = [str(plan.get("target_audience") or "").strip()]
    minutes = plan.get("duration_minutes")
    if minutes:
        meta.append(f"{minutes} 分钟")
    meta = [line for line in meta if line]
    if meta:
        _add_textbox(
            slide,
            Inches(0.75),
            Inches(3.05),
            Inches(3.3),
            Inches(1.4),
            "\n".join(meta),
            font_size=13,
            color=COLOR_ON_BAND,
            line_spacing=1.6,
        )

    title = slide.shapes.title
    if title is not None:
        _fill_placeholder(
            title,
            str(spec.get("title") or _plan_title(plan)),
            size=38,
            bold=True,
            color=COLOR_INK,
            line_spacing=1.15,
        )
    _add_block(slide, 5.15, 4.26, 1.3, 0.06, COLOR_PRIMARY)
    subtitle = str(spec.get("purpose") or plan.get("teaching_goal") or "").strip()
    if subtitle:
        placeholder = _body_placeholder(slide)
        if placeholder is not None:
            _fill_placeholder(
                placeholder, subtitle, size=15, color=COLOR_MUTED, line_spacing=1.35
            )

    # 封面要点：与正文同一套渲染（彩色圆点 + **强调**解析），字号随条数自适应；
    # 真的放不下的条目由调用方并入讲稿。
    bullets = [str(item).strip() for item in source_bullets if str(item).strip()]
    if not bullets:
        return []
    size, space_after = _cover_bullet_style(len(bullets))
    visible, hidden = _fit_cover_bullets(bullets, size=size, space_after=space_after)
    if visible:
        box = slide.shapes.add_textbox(
            Inches(COVER_BULLET_LEFT_IN),
            Inches(COVER_BULLET_TOP_IN),
            Inches(COVER_BULLET_WIDTH_IN),
            Inches(COVER_BULLET_BOTTOM_IN - COVER_BULLET_TOP_IN),
        )
        _fill_bullet_frame(box.text_frame, visible, size=size, space_after=space_after)
    return hidden


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


def _render_agenda(slide, spec, items: list) -> None:
    """Numbered overview page: placeholder title, drawn badges for the items."""
    from pptx.util import Inches

    _drop_body_placeholder(slide)
    _add_slide_title(slide, str(spec.get("title") or "本课结构"))
    _add_lead_in(slide, str(spec.get("purpose") or ""))
    for position, item in enumerate(items[:6], start=1):
        row_top = CONTENT_TOP_IN + (position - 1) * 0.63
        badge = _add_block(slide, CONTENT_LEFT_IN, row_top, 0.4, 0.4, COLOR_PRIMARY, oval=True)
        _set_shape_text(badge, str(position), size=13, bold=True, color=COLOR_ON_BAND)
        _add_textbox(
            slide,
            Inches(CONTENT_LEFT_IN + 0.64),
            Inches(row_top + 0.04),
            Inches(CONTENT_WIDTH_IN - 0.7),
            Inches(0.42),
            str(item),
            font_size=18,
            color=COLOR_INK,
        )


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
    if not _fill_body(slide, bullets, size=18):
        _add_bullets(slide, bullets, TEXT_BOXES["full"], size=18)


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

    新版式各有明确上限（卡片 4 张、流程 4 段、度量 3 块），而要点上限由
    `max_bullets_per_slide` 决定（可到 8 条）。超出的条目绝不能静默丢掉，
    调用方会把它们并入讲稿。
    """
    return entries[:limit], entries[limit:]


def _overflow_note(notes: str, items: list) -> str:
    """把版面放不下的条目并入讲稿，保证内容不因版式而消失。"""
    lines = "\n".join(f"• {strip_emphasis(item)}" for item in items)
    if not lines:
        return notes
    return f"本页版面容纳不下以下条目，已保留在讲稿中：\n{lines}\n\n{notes}".rstrip()


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
    body = _body_placeholder(slide)
    if body is not None:
        # 满版页已有渐变蒙层当底，再叠一层面板会把图片糊掉。
        if not full_bleed:
            _add_panel(slide, PANEL_BOXES[placement])
        # 占位符默认占满内容区；右侧图文要让出右半区。哪一页带图是逐页信息，
        # 版式层面无从预知，只能改这一页的占位符几何。
        body.left, body.top = Inches(left), Inches(top)
        body.width, body.height = Inches(width), Inches(height)
        _fill_bullet_frame(body.text_frame, bullets)
    else:
        if not full_bleed:
            _add_panel(slide, PANEL_BOXES[placement])
        _add_bullets(slide, bullets, (left, max(top, body_top), width, height))
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
    max_bullets = int(pptx_spec.get("max_bullets_per_slide") or 5)
    narrative_arc = [str(item) for item in pptx_spec.get("narrative_arc") or []]
    visual_direction = str(pptx_spec.get("visual_direction") or "")
    prs.core_properties.subject = " → ".join(narrative_arc)
    prs.core_properties.category = visual_direction
    plan_title = _plan_title(plan)
    total = len(slides)

    for index, spec in enumerate(slides):
        # 版式必须在建页之前定下来：占位符是创建幻灯片时按版式生成的。
        layout = _slide_layout(spec, index, total)

        image_spec = spec.get("image") or {}
        placement = str(image_spec.get("placement") or "right")
        if placement not in IMAGE_PLACEMENTS:
            placement = "right"
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
        bullets = [str(item) for item in source_bullets[:max_bullets]]
        background_embedded = False
        # 版式各有容量上限（卡片 4、流程 4、度量 3），放不下的条目在这里收集，
        # 稍后并入讲稿。
        overflow: list = []

        if layout == "cover":
            # 传未截断的 source_bullets：超过 max_bullets 的条目也要进讲稿，不能凭空消失。
            overflow = _render_cover(
                slide, spec, plan, picture, [str(item) for item in source_bullets]
            )
        elif layout == "section":
            _render_section(slide, spec, picture)
        else:
            _add_chrome(slide, plan_title, index + 1, total)
            _add_sources(slide, spec.get("evidence_refs") or [])
            if layout == "agenda":
                _render_agenda(slide, spec, bullets)
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
</style>
</head>
<body>
<main>
  <h1>{title}</h1>
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
