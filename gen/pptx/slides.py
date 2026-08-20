"""
M5 - PPT 页面模板定义 (Owner: 赵钰洁)
负责定义 4 种基础幻灯片页面的组装逻辑：封面、目录、内容、总结
"""
from pptx import Presentation


def create_cover_slide(prs: Presentation, title: str, subtitle: str):
    """
    生成【封面页】
    """
    # 布局 0 通常是“标题幻灯片”
    slide_layout = prs.slide_layouts[0]
    slide = prs.slides.add_slide(slide_layout)

    title_shape = slide.shapes.title
    subtitle_shape = slide.placeholders[1]

    title_shape.text = title
    subtitle_shape.text = subtitle
    return slide


def create_catalog_slide(prs: Presentation, chapters: list[str]):
    """
    生成【目录页】
    """
    # 布局 1 通常是“标题和内容”
    slide_layout = prs.slide_layouts[1]
    slide = prs.slides.add_slide(slide_layout)

    slide.shapes.title.text = "目录 / Catalog"
    body_shape = slide.placeholders[1]
    tf = body_shape.text_frame

    # 写入目录列表
    for i, chapter in enumerate(chapters):
        if i == 0:
            tf.text = f"{i + 1}. {chapter}"
        else:
            p = tf.add_paragraph()
            p.text = f"{i + 1}. {chapter}"
            p.level = 0

    return slide


def create_content_slide(prs: Presentation, heading: str, bullet_points: list[str]):
    """
    生成【核心内容页】
    """
    slide_layout = prs.slide_layouts[1]
    slide = prs.slides.add_slide(slide_layout)

    slide.shapes.title.text = heading
    body_shape = slide.placeholders[1]
    tf = body_shape.text_frame

    # 逐行写入知识点
    for i, point in enumerate(bullet_points):
        if i == 0:
            tf.text = point
        else:
            p = tf.add_paragraph()
            p.text = point

    return slide


def create_summary_slide(prs: Presentation, summary_text: str):
    """
    生成【课堂总结页】
    """
    slide_layout = prs.slide_layouts[1]
    slide = prs.slides.add_slide(slide_layout)

    slide.shapes.title.text = "本节小结"
    body_shape = slide.placeholders[1]
    body_shape.text_frame.text = summary_text

    return slide
