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
    font_name="Microsoft YaHei",
    align=None,
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
    if align is not None:
        paragraph.alignment = align
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
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    slides = _slides(plan)
    pptx_spec = (plan.get("output_specs") or {}).get("pptx") or {}
    max_bullets = int(pptx_spec.get("max_bullets_per_slide") or 5)
    narrative_arc = [str(item) for item in pptx_spec.get("narrative_arc") or []]
    visual_direction = str(pptx_spec.get("visual_direction") or "")
    prs.core_properties.subject = " → ".join(narrative_arc)
    prs.core_properties.category = visual_direction

    for index, spec in enumerate(slides):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        layout = str(spec.get("layout") or "title_and_bullets")
        title_size = 50 if index == 0 or layout == "cover" else 35
        _add_textbox(
            slide,
            Inches(0.65),
            Inches(0.55),
            Inches(12.0),
            Inches(0.9),
            spec.get("title", "教学内容"),
            font_size=title_size,
            bold=True,
        )
        source_bullets = narrative_arc if layout == "agenda" and narrative_arc else spec.get("bullets") or []
        bullets = source_bullets[:max_bullets]
        body = slide.shapes.add_textbox(Inches(0.9), Inches(1.75), Inches(11.6), Inches(4.15))
        frame = body.text_frame
        frame.word_wrap = True
        frame.margin_left = 0
        frame.margin_right = 0
        frame.margin_top = 0
        frame.margin_bottom = 0
        frame.clear()
        for bullet_index, bullet in enumerate(bullets):
            paragraph = frame.paragraphs[0] if bullet_index == 0 else frame.add_paragraph()
            prefix = "" if layout in {"cover", "agenda"} else "• "
            paragraph.text = prefix + str(bullet)
            paragraph.level = 0
            paragraph.font.size = Pt(24 if index == 0 else 22)
            paragraph.font.name = "Microsoft YaHei"
            paragraph.space_after = Pt(12)

        _add_textbox(
            slide,
            Inches(0.65),
            Inches(6.75),
            Inches(10.2),
            Inches(0.35),
            _source_line(spec.get("evidence_refs") or []),
            font_size=9,
        )
        notes = spec.get("speaker_notes") or ""
        if index == 0 and visual_direction:
            notes = f"视觉方向：{visual_direction}\n{notes}"
        if notes:
            slide.notes_slide.notes_text_frame.text = str(notes)
        _add_textbox(
            slide,
            Inches(11.45),
            Inches(6.75),
            Inches(1.2),
            Inches(0.35),
            f"{index + 1} / {len(slides)}",
            font_size=9,
            align=PP_ALIGN.RIGHT,
        )

    output_path = settings.output_dir / (output_name or f"ppt_{uuid.uuid4().hex[:24]}.pptx")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    logger.info("Generated PPT: %s", output_path)
    return str(output_path)


async def generate_pdf(
    plan: dict,
    rag_docs: list | None = None,
    references: list | None = None,
    *,
    output_name: str | None = None,
) -> str:
    """Render a printable lesson document as a real PDF."""
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
        <footer>第 {page_number} / {len(pages)} 页</footer>
        """
        page.insert_htmlbox(fitz.Rect(48, 48, 547, 794), markup)

    output_path = settings.output_dir / (output_name or f"lesson_{uuid.uuid4().hex[:24]}.pdf")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output_path), garbage=4, deflate=True)
    document.close()
    logger.info("Generated PDF: %s", output_path)
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

    output_path = settings.output_dir / (output_name or f"doc_{uuid.uuid4().hex[:24]}.docx")
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
