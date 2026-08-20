"""M4 renderers driven by one CoursewarePlan JSON document."""

from __future__ import annotations

import html as html_lib
import json
import logging
import uuid

from backend.config import settings

logger = logging.getLogger(__name__)


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


def _add_textbox(slide, left, top, width, height, text, *, font_size=22, bold=False):
    from pptx.util import Pt

    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.word_wrap = True
    frame.clear()
    paragraph = frame.paragraphs[0]
    paragraph.text = str(text)
    paragraph.font.size = Pt(font_size)
    paragraph.font.bold = bold
    return box


async def generate_pptx(
    plan: dict,
    rag_docs: list | None = None,
    references: list | None = None,
    *,
    output_name: str | None = None,
) -> str:
    """Render all SlideSpec entries from a CoursewarePlan into a PPTX."""
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    slides = _slides(plan)

    for index, spec in enumerate(slides):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        title_size = 36 if index == 0 else 28
        _add_textbox(
            slide,
            Inches(0.65),
            Inches(0.55),
            Inches(12.0),
            Inches(0.8),
            spec.get("title", "教学内容"),
            font_size=title_size,
            bold=True,
        )
        bullets = spec.get("bullets") or []
        body = slide.shapes.add_textbox(Inches(0.9), Inches(1.7), Inches(11.6), Inches(4.7))
        frame = body.text_frame
        frame.word_wrap = True
        frame.clear()
        for bullet_index, bullet in enumerate(bullets):
            paragraph = frame.paragraphs[0] if bullet_index == 0 else frame.add_paragraph()
            paragraph.text = str(bullet)
            paragraph.level = 0
            paragraph.font.size = Pt(22 if index == 0 else 20)
            paragraph.space_after = Pt(12)

        _add_textbox(
            slide,
            Inches(0.65),
            Inches(6.75),
            Inches(12.0),
            Inches(0.35),
            _source_line(spec.get("evidence_refs") or []),
            font_size=9,
        )
        notes = spec.get("speaker_notes") or ""
        if notes:
            _add_textbox(
                slide,
                Inches(0.9),
                Inches(6.25),
                Inches(11.6),
                Inches(0.35),
                f"讲稿：{notes}",
                font_size=10,
            )

    output_path = settings.output_dir / (output_name or f"ppt_{uuid.uuid4().hex[:8]}.pptx")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    logger.info("Generated PPT: %s", output_path)
    return str(output_path)


async def generate_docx(
    plan: dict,
    rag_docs: list | None = None,
    references: list | None = None,
    *,
    output_name: str | None = None,
) -> str:
    """Render LessonPlanSectionSpec entries from a CoursewarePlan into DOCX."""
    from docx import Document

    doc = Document()
    doc.add_heading(_plan_title(plan), level=0)
    doc.add_paragraph(
        f"授课对象：{plan.get('target_audience', '')}  |  "
        f"课时：{plan.get('duration_minutes', 45)} 分钟"
    )
    doc.add_heading("一、教学目标", level=1)
    doc.add_paragraph(str(plan.get("teaching_goal") or _plan_title(plan)))
    doc.add_heading("二、教学重点与难点", level=1)
    doc.add_paragraph(f"重点：{plan.get('teaching_focus') or '围绕核心知识点建立理解'}")
    doc.add_paragraph(f"难点：{plan.get('teaching_difficulties') or '将知识迁移到新情境'}")

    doc.add_heading("三、教学过程", level=1)
    sections = plan.get("lesson_sections") or []
    for section in sections:
        doc.add_heading(
            f"{section.get('order', 1)}. {section.get('title', '教学环节')} "
            f"（{section.get('duration_minutes', 1)} 分钟）",
            level=2,
        )
        doc.add_paragraph(f"目标：{section.get('objective', '')}")
        doc.add_paragraph("教师活动：" + "；".join(section.get("teacher_actions") or []))
        doc.add_paragraph("学生活动：" + "；".join(section.get("student_actions") or []))
        doc.add_paragraph(f"评价：{section.get('assessment', '')}")
        doc.add_paragraph(_source_line(section.get("evidence_refs") or []))

    doc.add_heading("四、互动练习", level=1)
    for interaction in plan.get("interactions") or []:
        doc.add_paragraph(str(interaction.get("title", "互动练习")))
        doc.add_paragraph(str(interaction.get("prompt", "")))
        for item in interaction.get("items") or []:
            doc.add_paragraph(str(item), style="List Bullet")

    doc.add_heading("五、来源", level=1)
    refs = plan.get("evidence_refs") or []
    if refs:
        for ref in refs:
            doc.add_paragraph(_source_label(ref), style="List Bullet")
    else:
        doc.add_paragraph("暂无可用证据")

    output_path = settings.output_dir / (output_name or f"doc_{uuid.uuid4().hex[:8]}.docx")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    logger.info("Generated DOCX: %s", output_path)
    return str(output_path)


async def generate_html(
    plan: dict,
    rag_docs: list | None = None,
    references: list | None = None,
    *,
    output_name: str | None = None,
) -> str:
    """Render an executable classification activity from InteractionSpec."""
    title = html_lib.escape(_plan_title(plan))
    interaction = (plan.get("interactions") or [{}])[0]
    interaction_title = html_lib.escape(str(interaction.get("title") or "互动练习"))
    prompt = html_lib.escape(str(interaction.get("prompt") or "请完成本课互动练习"))
    items = [str(item) for item in interaction.get("items") or []]
    item_json = json.dumps(items, ensure_ascii=False)
    source_text = html_lib.escape(_source_line(interaction.get("evidence_refs") or []))
    item_markup = "".join(
        f'<button class="item" data-index="{index}">{html_lib.escape(item)}</button>'
        for index, item in enumerate(items)
    )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} - 互动练习</title>
<style>
  body {{ margin: 0; padding: 32px; background: #f4f7fb; color: #172033; font-family: Arial, "Microsoft YaHei", sans-serif; }}
  main {{ max-width: 760px; margin: 0 auto; background: #fff; padding: 32px; border: 1px solid #dfe6f0; border-radius: 8px; }}
  h1 {{ margin-top: 0; }}
  .item {{ display: block; width: 100%; margin: 10px 0; padding: 14px 16px; text-align: left; border: 1px solid #cad5e5; border-radius: 6px; background: #fff; cursor: pointer; }}
  .item:hover, .item.selected {{ border-color: #1463ff; background: #eef4ff; }}
  #result {{ min-height: 24px; margin-top: 20px; color: #1463ff; }}
  .source {{ margin-top: 24px; color: #6b778c; font-size: 12px; }}
</style>
</head>
<body>
<main>
  <h1>{title}</h1>
  <h2>{interaction_title}</h2>
  <p>{prompt}</p>
  <section id="items">{item_markup}</section>
  <p id="result" aria-live="polite"></p>
  <p class="source">{source_text}</p>
</main>
<script>
const items = {item_json};
const selected = [];
document.querySelectorAll('.item').forEach((button) => {{
  button.addEventListener('click', () => {{
    const index = Number(button.dataset.index);
    button.classList.toggle('selected');
    if (selected.includes(index)) selected.splice(selected.indexOf(index), 1);
    else selected.push(index);
    document.querySelector('#result').textContent = `已选择 ${{selected.length}} / ${{items.length}} 项`;
  }});
}});
</script>
</body>
</html>"""
    output_path = settings.output_dir / (
        output_name or f"interactive_{uuid.uuid4().hex[:8]}.html"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("Generated HTML: %s", output_path)
    return str(output_path)
