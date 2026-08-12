"""M2 — 多模态文件解析器"""
import os
import io
import re
import base64
import logging
import fitz  # PyMuPDF
from docx import Document
from PIL import Image
from openai import OpenAI

# 引入配置好的大模型环境变量
from backend.config import settings

# 初始化日志记录器
logger = logging.getLogger(__name__)

def encode_image_to_base64(image_path: str) -> str:
    """把图片转成大模型能看懂的 Base64 编码"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

async def parse_image(file_path: str) -> dict:
    """解析图片：调用视觉大模型，强制处理公式(LaTeX)与图表数据(Markdown)"""
    if not os.path.exists(file_path):
        logger.error(f"图片文件不存在: {file_path}")
        return {"status": "error", "message": "图片文件不存在", "data": ""}

    try:
        client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url
        )

        base64_image = encode_image_to_base64(file_path)

        # 👑 针对痛点2和3的终极提示词（强制 LaTeX 与 图表数据化）
        vision_prompt = """
        你是一个专业的高校教学资料多模态解析专家。请详细解析图片内容，并严格遵守以下3条绝对规则：
        1. 【公式防乱码】：如果图片中包含任何数学公式、微积分、物理或化学反应式，必须严格使用标准的 LaTeX 代码格式输出（行内公式用 $ 包裹，独立公式用 $$ 包裹），严禁输出乱码符号。
        2. 【图表防失真】：如果图片是图表（如折线图、柱状图、流程图），严禁只做模糊的主观描述！必须提取出坐标轴名称、核心维度，并尽可能以 Markdown 表格的形式还原图表中的实际数据节点。
        3. 【结构不丢失】：提取正文时，请使用 Markdown 标记（如 # 标题、* 列表）保留原图的视觉层级和逻辑排版。
        """

        response = client.chat.completions.create(
            model="deepseek-chat", # 建议后续确保使用的是支持视觉的多模态模型(如 qwen-vl-plus)
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": vision_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=1000
        )

        result_text = response.choices[0].message.content
        return {"status": "success", "message": "识别成功", "data": result_text}

    except Exception as e:
        logger.error(f"AI API 请求失败: {str(e)}")
        return {"status": "error", "message": f"API 请求失败: {str(e)}", "data": ""}

async def parse_pdf(file_path: str) -> str:
    """解析 PDF 文件，提取文本与图片信息 (根据字号保留 Markdown 标题层级)"""
    if not os.path.exists(file_path):
        logger.error(f"PDF 文件不存在: {file_path}")
        raise FileNotFoundError(f"文件不存在: {file_path}")

    doc = fitz.open(file_path)
    full_content = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        full_content.append(f"\n\n=========== 第 {page_num + 1} 页 ===========\n")

        blocks = page.get_text("dict")["blocks"]
        seen_bboxes = set()
        real_img_count = 0

        for block in blocks:
            # 👑 针对痛点1：根据字号大小，动态赋予 Markdown 标题层级，防止结构丢失
            if block["type"] == 0:
                block_text = ""
                max_font_size = 0
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        block_text += span.get("text", "")
                        if span.get("size", 0) > max_font_size:
                            max_font_size = span.get("size", 0)
                    block_text += "\n"

                block_text = block_text.strip()
                if not block_text:
                    continue

                if max_font_size >= 15:
                    full_content.append(f"\n# {block_text}\n\n")
                elif max_font_size >= 12:
                    full_content.append(f"\n## {block_text}\n\n")
                else:
                    full_content.append(f"{block_text}\n\n")

            # 提取图片并送去多模态解析（处理公式与图表）
            elif block["type"] == 1:
                bbox = tuple(round(x, 1) for x in block["bbox"])
                width = bbox[2] - bbox[0]
                height = bbox[3] - bbox[1]

                if width < 50 or height < 50 or bbox in seen_bboxes:
                    continue
                seen_bboxes.add(bbox)

                image_bytes = block.get("image")
                if not image_bytes:
                    continue

                real_img_count += 1
                image_filename = f"extracted_pdf_p{page_num + 1}_img{real_img_count}.png"

                try:
                    img_obj = Image.open(io.BytesIO(image_bytes))
                    if img_obj.mode not in ('RGB', 'RGBA'):
                        img_obj = img_obj.convert('RGBA')
                    img_obj.save(image_filename, format="PNG")

                    ai_result = await parse_image(image_filename)
                    if ai_result["status"] == "success":
                        full_content.append(f"\n【多模态解析内容(含公式/图表数据)】:\n{ai_result['data']}\n\n")
                except Exception as e:
                    logger.warning(f"PDF 图片解析异常: {e}")
                    continue
                finally:
                    if os.path.exists(image_filename):
                        os.remove(image_filename)

    doc.close()
    return "".join(full_content).strip()

async def parse_docx(file_path: str) -> str:
    """解析 Word 文档，提取文本、图片，并将表格强制转为 Markdown 格式"""
    if not os.path.exists(file_path):
        logger.error(f"Word 文件不存在: {file_path}")
        raise FileNotFoundError(f"文件不存在: {file_path}")

    doc = Document(file_path)
    content_blocks = []

    # 1. 提取文字排版 (保留列表和缩进)
    for para in doc.paragraphs:
        text = para.text.rstrip()
        if not text.strip():
            continue

        if not text.startswith(" ") and not text.startswith("\t"):
            has_indent = False
            try:
                fmt = para.paragraph_format
                if (fmt.first_line_indent and fmt.first_line_indent > 0) or \
                   (fmt.left_indent and fmt.left_indent > 0):
                    has_indent = True
            except Exception:
                pass
            if has_indent:
                text = "    " + text

        clean_text = text.strip()
        if len(content_blocks) == 0:
            content_blocks.append(f"{text}\n\n")
        elif re.match(r'^[一二三四五六七八九十]+、', clean_text):
            content_blocks.append(f"\n### {text}\n")
        else:
            content_blocks.append(f"{text}\n")

    # 👑 针对痛点1：提取表格，并强制转换为标准的 Markdown 表格语法
    if doc.tables:
        for table in doc.tables:
            content_blocks.append("\n**【文档表格数据】**\n")
            for i, row in enumerate(table.rows):
                row_data = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
                # 拼接成 | 单元格 | 单元格 | 的格式
                content_blocks.append("| " + " | ".join(row_data) + " |\n")
                if i == 0:
                    # 第一行结束后，加上 Markdown 的表头分割线
                    content_blocks.append("|" + "|".join(["---"] * len(row.cells)) + "|\n")
            content_blocks.append("\n")

    # 3. 提取图片送去多模态解析
    img_count = 0
    for rel in doc.part.rels.values():
        if "image" in rel.target_ref:
            img_count += 1
            image_bytes = rel.target_part.blob
            image_filename = f"extracted_word_img{img_count}.png"
            try:
                img_obj = Image.open(io.BytesIO(image_bytes))
                if img_obj.mode not in ('RGB', 'RGBA'):
                    img_obj = img_obj.convert('RGBA')
                img_obj.save(image_filename, format="PNG")

                ai_result = await parse_image(image_filename)
                if ai_result["status"] == "success":
                    content_blocks.append(f"\n\n【多模态解析内容(含公式/图表数据)】:\n{ai_result['data']}\n\n")
            except Exception as e:
                logger.warning(f"Word 图片解析异常: {e}")
                continue
            finally:
                if os.path.exists(image_filename):
                    os.remove(image_filename)

    return "".join(content_blocks).strip()

async def parse_video(file_path: str) -> str:
    """解析视频（提取关键帧 + 音频转录）"""
    try:
        import subprocess
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", file_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            import json
            info = json.loads(result.stdout)
            duration = round(float(info.get("format", {}).get("duration", 0)))
            return f"[视频] 时长: {duration}秒"
        return f"[视频] 无法解析: {file_path}"
    except Exception as e:
        logger.warning("Video parsing failed: %s", e)
        return f"[视频解析失败: {file_path}]"
