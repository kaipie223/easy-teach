import os
import io
import re
from docx import Document
from PIL import Image
from gen.parse.image import parse_image


def parse_comprehensive_word(file_path: str) -> dict:
    if not os.path.exists(file_path):
        return {"status": "error", "message": f"文件不存在: {file_path}", "data": ""}

    try:
        doc = Document(file_path)
        content_blocks = []

        for i, para in enumerate(doc.paragraphs):
            # 【核心修复 1】：绝不用 strip()！改用 rstrip() 只删右侧换行，死死保住左侧的物理空格
            text = para.text.rstrip()
            if not text.strip():
                continue

            # 【核心修复 2】：读取 Word 底层段落的“首行缩进”或“左侧缩进”
            # 如果段落开头没有手动敲的空格，我们去底层查一下有没有设置缩进
            if not text.startswith(" ") and not text.startswith("\t"):
                has_indent = False
                try:
                    fmt = para.paragraph_format
                    if (fmt.first_line_indent and fmt.first_line_indent > 0) or \
                            (fmt.left_indent and fmt.left_indent > 0):
                        has_indent = True
                except Exception:
                    pass

                # 如果底层设置了缩进，我们强行用 4 个空格把它顶过去
                if has_indent:
                    text = "    " + text

            # 【核心修复 3】：针对中文报告文档的智能换行识别
            clean_text = text.strip()

            # 1. 识别主标题（第一段），在它下方留一个空行
            if len(content_blocks) == 0:
                content_blocks.append(f"{text}\n\n")
            # 2. 识别中文大纲（如：一、二、），在它上方留空行，凸显层级
            elif re.match(r'^[一二三四五六七八九十]+、', clean_text):
                content_blocks.append(f"\n{text}\n")
            # 3. 普通正文或手动换行，按原样输出
            else:
                content_blocks.append(f"{text}\n")

        # ==========================================
        # 提取表格 (保持之前的空格对齐逻辑)
        # ==========================================
        if doc.tables:
            for table in doc.tables:
                content_blocks.append("\n")
                for row in table.rows:
                    row_data = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
                    content_blocks.append("    ".join(row_data) + "\n")
                content_blocks.append("\n")

        # ==========================================
        # 提取图片 -> 存为PNG -> AI识别 (原封不动)
        # ==========================================
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
                except Exception as img_e:
                    content_blocks.append(f"\n[图片转换 PNG 失败: {str(img_e)}]\n")
                    continue

                ai_result = parse_image(image_filename)

                if ai_result["status"] == "success":
                    content_blocks.append(f"\n\n【文档图片 AI 识别内容】:\n{ai_result['data']}\n\n")
                else:
                    content_blocks.append(f"\n\n【文档图片 AI 识别失败】: {ai_result['message']}\n\n")

        final_text = "".join(content_blocks).strip()

        return {
            "status": "success",
            "message": "Word 解析完成",
            "data": final_text
        }

    except Exception as e:
        return {"status": "error", "message": f"Word解析失败: {str(e)}", "data": ""}


if __name__ == "__main__":
    result = parse_comprehensive_word("test_document.docx")
    print(result["data"])