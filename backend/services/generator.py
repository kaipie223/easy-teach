"""M5 — 课件生成器（PPT + Word + HTML 动画）"""

import logging

from backend.config import settings

logger = logging.getLogger(__name__)


async def generate_pptx(intent: dict, rag_docs: list, references: list) -> str:
    """生成 PPT 课件，返回文件路径。

    Args:
        intent: 意图字典
        rag_docs: RAG 检索结果
        references: 参考资料

    Returns:
        生成的 .pptx 文件路径
    """
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt

        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)

        # 封面页
        slide_layout = prs.slide_layouts[0]
        slide = prs.slides.add_slide(slide_layout)
        title = slide.shapes.title
        if title:
            title.text = intent.get("teaching_goal", "课件")
            title.text_frame.paragraphs[0].font.size = Pt(44)

        # 内容页：每个知识点一页
        for kp in intent.get("knowledge_points", []):
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            shapes = slide.shapes
            if shapes.title:
                shapes.title.text = kp.get("title", "知识点")

        # 保存
        import uuid
        output_path = settings.output_dir / f"ppt_{uuid.uuid4().hex[:8]}.pptx"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        prs.save(str(output_path))
        logger.info("Generated PPT: %s", output_path)
        return str(output_path)
    except ImportError:
        logger.warning("python-pptx not installed")
        return "[PPT 需要 python-pptx 库]"
    except Exception:
        logger.exception("PPT generation failed")
        raise


async def generate_docx(intent: dict, rag_docs: list, references: list) -> str:
    """生成 Word 教案，返回文件路径。"""
    try:
        from docx import Document

        doc = Document()
        doc.add_heading(intent.get("teaching_goal", "教案"), level=0)

        doc.add_heading("一、教学目标", level=1)
        doc.add_paragraph(f"教学目标: {intent.get('teaching_goal', '')}")

        doc.add_heading("二、教学重难点", level=1)
        for kp in intent.get("knowledge_points", []):
            doc.add_paragraph(f"• {kp.get('title', '')} ({kp.get('difficulty', 'basic')})")

        doc.add_heading("三、教学过程", level=1)
        for step in intent.get("logic_flow", []):
            doc.add_heading(step, level=2)
            doc.add_paragraph("（教学内容待补充）")

        import uuid
        output_path = settings.output_dir / f"doc_{uuid.uuid4().hex[:8]}.docx"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
        logger.info("Generated DOCX: %s", output_path)
        return str(output_path)
    except ImportError:
        logger.warning("python-docx not installed")
        return "[Word 需要 python-docx 库]"
    except Exception:
        logger.exception("DOCX generation failed")
        raise


async def generate_html(intent: dict, rag_docs: list, references: list) -> str:
    """生成 HTML 动画/互动游戏，返回文件路径。"""
    import uuid

    topic = intent.get("teaching_goal", "知识闯关")
    kps = intent.get("knowledge_points", [])

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{topic} - 互动练习</title>
<style>
  body {{ font-family: 'Microsoft YaHei', sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f7fa; }}
  h1 {{ color: #2c3e50; text-align: center; }}
  .quiz {{ background: white; border-radius: 12px; padding: 24px; margin: 16px 0; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
  .quiz h3 {{ color: #3498db; margin-top: 0; }}
  .quiz p {{ color: #555; line-height: 1.6; }}
  .key-point {{ display: inline-block; background: #e8f4fd; color: #2980b9; padding: 4px 12px; border-radius: 16px; margin: 4px; font-size: 14px; }}
  button {{ display: block; margin: 24px auto; padding: 12px 48px; background: #3498db; color: white; border: none; border-radius: 8px; font-size: 18px; cursor: pointer; }}
  button:hover {{ background: #2980b9; }}
</style>
</head>
<body>
<h1>🎓 {topic}</h1>
<p style="text-align:center;color:#888;">互动练习 · 自动生成</p>
"""
    for i, kp in enumerate(kps):
        html += f"""
<div class="quiz">
  <h3>{i+1}. {kp.get('title', '知识点')}</h3>
  <p>{'; '.join(kp.get('key_points', ['请在此处添加要点']))}</p>
  <div>{" ".join(f'<span class="key-point">{e}</span>' for e in kp.get('examples', []))}</div>
</div>"""

    html += """
<button onclick="alert('恭喜完成！')">✅ 我已掌握</button>
</body>
</html>"""

    output_path = settings.output_dir / f"anim_{uuid.uuid4().hex[:8]}.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("Generated HTML: %s", output_path)
    return str(output_path)
