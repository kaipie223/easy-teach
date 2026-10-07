"""PPTX 版面质量闸门。

这些指标是从真实导出件里量出来的问题，不是审美偏好：

- 没有配图时正文必须占满整幅宽度。以前无论有没有图都按 "right" 排，13.3in 的页面
  右半幅永远空着，正文被挤进 4.98in 窄栏，字号一路被压到 12pt（投影读不清）。
- 页眉与页码要看得见：9pt 在教室后排等于没有。
- 正文（要点）不得低于 12pt，单条超长时宁可拆成续页。

量的是"文件里真实写进去的字号与几何"，所以它挡得住以后任何一次回退。
"""

from math import ceil

from pptx import Presentation
from pptx.util import Emu

from backend.config import settings
from backend.services.generator import (
    BODY_MIN_SIZE,
    CHROME_FONT_SIZE,
    TEXT_BOXES,
    generate_pptx,
)

EMU_PER_IN = 914400


def _payload(slide_count: int = 6, *, with_image: bool = False) -> dict:
    slides = []
    for index in range(1, slide_count + 1):
        slide = {
            "slide_id": f"slide_{index:03d}",
            "order": index,
            "layout": "cover" if index == 1 else ("summary" if index == slide_count else "bullets"),
            "title": f"第 {index} 页标题",
            "purpose": f"第 {index} 页的导语。",
            "bullets": [
                f"第 {index} 页要点一：说明这个概念的含义与适用条件，并给出一个具体例子。",
                f"第 {index} 页要点二：写出判断依据与常见错误，便于课堂对照讲解。",
                f"第 {index} 页要点三：补充一条迁移应用，让学生说出它的前提。",
            ],
            "speaker_notes": f"第 {index} 页讲稿：" + "先提问再讲解，预设回答并纠正。" * 4,
        }
        if with_image and index not in (1, slide_count):
            slide["image"] = {"material_id": "mat_1", "placement": "right", "caption": "示意图"}
        slides.append(slide)
    return {
        "title": "版面质量用课件",
        "target_audience": "初二学生",
        "duration_minutes": 45,
        "teaching_goal": "验证版面质量闸门",
        "knowledge_points": [{"point_id": "kp_1", "order": 1, "title": "知识点"}],
        "slides": slides,
        "lesson_sections": [
            {
                "section_id": "sec_1",
                "order": 1,
                "title": "导入",
                "duration_minutes": 45,
                "objective": "学生能说出问题",
                "teacher_actions": ["提问"],
                "student_actions": ["回答"],
                "assessment": "能回答即达标",
            }
        ],
        "interactions": [
            {
                "interaction_id": "interaction_001",
                "interaction_type": "classification",
                "title": "分类",
                "prompt": "归类。",
                "items": ["甲"],
                "answer_groups": {"对": ["甲"]},
            }
        ],
        "output_specs": {"pptx": {"max_bullets_per_slide": 5, "narrative_arc": ["导入", "讲解"]}},
    }


def _deck(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    path = generate_pptx(_payload(**kwargs), output_name="quality.pptx")
    return Presentation(path)


def _text_sizes(shape) -> list[float]:
    sizes: list[float] = []
    for paragraph in shape.text_frame.paragraphs:
        if paragraph.font.size is not None:
            sizes.append(paragraph.font.size.pt)
        for run in paragraph.runs:
            if run.font.size is not None and run.text.strip() not in {"●", "·", "▪"}:
                sizes.append(run.font.size.pt)
    return sizes


def test_body_takes_full_width_when_there_is_no_image(tmp_path, monkeypatch):
    """没有配图时正文占满整幅：窄栏会让右半页空着、字号被迫变小。"""
    presentation = _deck(tmp_path, monkeypatch, with_image=False)
    full_width = TEXT_BOXES["full"][2]
    narrow = 0
    for slide in presentation.slides:
        if slide.slide_layout.name == "Title Slide":
            continue  # 封面要点本来就是右侧窄栏，属于设计
        for shape in slide.shapes:
            if not shape.has_text_frame or "●" not in shape.text_frame.text:
                continue
            if shape.top is None or shape.top / EMU_PER_IN > 6.8:
                continue
            if shape.width / EMU_PER_IN < full_width - 0.1:
                narrow += 1
    assert narrow == 0, "无配图的页面仍把正文排进了窄栏"


def test_body_keeps_full_width_even_when_image_is_requested_but_missing(tmp_path, monkeypatch):
    """蓝图要求了配图但素材取不到时，也不该把正文留在窄栏里空等图片。"""
    presentation = _deck(tmp_path, monkeypatch, with_image=True)  # images 未传入 -> 全部取不到
    full_width = TEXT_BOXES["full"][2]
    widths = [
        shape.width / EMU_PER_IN
        for slide in presentation.slides
        for shape in slide.shapes
        if shape.has_text_frame and "●" in shape.text_frame.text and shape.top is not None
    ]
    assert widths and max(widths) >= full_width - 0.1


def test_chrome_and_body_font_floors(tmp_path, monkeypatch):
    """页眉页码不低于 CHROME_FONT_SIZE，正文要点不低于 BODY_MIN_SIZE。"""
    presentation = _deck(tmp_path, monkeypatch)
    chrome_sizes: list[float] = []
    body_sizes: list[float] = []
    for slide in presentation.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            sizes = _text_sizes(shape)
            if not sizes:
                continue
            top_in = shape.top / EMU_PER_IN if shape.top is not None else 0
            if top_in > 6.8:
                chrome_sizes.extend(sizes)
            elif "●" in shape.text_frame.text:
                body_sizes.extend(sizes)
    assert chrome_sizes and min(chrome_sizes) >= CHROME_FONT_SIZE
    assert body_sizes and min(body_sizes) >= BODY_MIN_SIZE


def test_long_title_shrinks_instead_of_overlapping_the_lead_in(tmp_path, monkeypatch):
    """长标题必须缩字号，不能换行后压到导语与正文面板上。

    以前标题固定 29pt；python-pptx 不会自动缩，长标题换行就顶到下面 ——
    "字重叠"就是这么来的。这里按"估算行数 × 字号"验证它装得进标题框。
    """
    from backend.services.generator import (
        CONTENT_WIDTH_IN,
        TITLE_HEIGHT_IN,
        _title_size,
    )

    long_title = "浮力产生的原因、阿基米德原理的适用条件与物体浮沉判断的完整推导过程"
    size = _title_size(long_title, width_in=CONTENT_WIDTH_IN, height_in=TITLE_HEIGHT_IN)
    assert size < 29, "长标题没有缩字号"

    chars_per_line = max(8, int(CONTENT_WIDTH_IN * 72 / size))
    lines = max(1, ceil(len(long_title) / chars_per_line))
    assert lines * size * 1.25 <= TITLE_HEIGHT_IN * 72, (
        f"标题仍会溢出：{lines} 行 × {size}pt"
    )

    # 短标题保持大字号，不要为了长标题把整册都变小
    short_title = "什么是浮力"
    assert _title_size(short_title, width_in=CONTENT_WIDTH_IN, height_in=TITLE_HEIGHT_IN) == 29


def test_dense_page_splits_into_continuation_instead_of_shrinking(tmp_path, monkeypatch):
    """一页塞不下时拆成续页，而不是把字号一路缩小。"""
    payload = _payload(slide_count=4)
    long_bullet = "这是很长的一条要点，" + "补充说明、适用条件与常见错误，" * 12
    payload["slides"][2]["bullets"] = [long_bullet] * 6
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    presentation = Presentation(generate_pptx(payload, output_name="split.pptx"))
    titles = [
        shape.text_frame.text
        for slide in presentation.slides
        for shape in slide.shapes
        if shape.has_text_frame and "续" in shape.text_frame.text
    ]
    assert titles, "过密的页面没有被拆成续页"
    # 拆页后正文仍不低于下限
    body_sizes = [
        size
        for slide in presentation.slides
        for shape in slide.shapes
        if shape.has_text_frame and "●" in shape.text_frame.text
        for size in _text_sizes(shape)
    ]
    assert min(body_sizes) >= BODY_MIN_SIZE
