import os
import fitz  # PyMuPDF
import io
from PIL import Image
from gen.parse.image import parse_image


def parse_smart_pdf(file_path: str) -> dict:
    if not os.path.exists(file_path):
        return {"status": "error", "message": f"文件不存在: {file_path}", "data": ""}

    try:
        doc = fitz.open(file_path)
        full_content = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            full_content.append(f"\n\n=========== 第 {page_num + 1} 页 ===========\n")

            blocks = page.get_text("dict")["blocks"]

            seen_bboxes = set()  # 记录图片坐标，物理防线
            real_img_count = 0

            for block in blocks:
                # ==========================================
                # 【文字块处理】：字号检测保层级 + 段落严格换行
                # ==========================================
                if block["type"] == 0:
                    block_text = ""
                    max_font_size = 0

                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            block_text += span.get("text", "")
                            # 找出这段话里最大的字号，用来判断是不是标题
                            if span.get("size", 0) > max_font_size:
                                max_font_size = span.get("size", 0)
                        # 保证 PDF 里的真实换行不被吃掉
                        block_text += "\n"

                    block_text = block_text.strip()
                    if not block_text:
                        continue

                    # 恢复层级：大字号加 # 变标题，并在每段最后加 \n\n 保证段落间距
                    if max_font_size >= 15:
                        full_content.append(f"\n# {block_text}\n\n")
                    elif max_font_size >= 12:
                        full_content.append(f"\n## {block_text}\n\n")
                    else:
                        full_content.append(f"{block_text}\n\n")

                # ==========================================
                # 【图片块处理】：坐标去重，只给 AI 正常图片
                # ==========================================
                elif block["type"] == 1:
                    bbox = tuple(round(x, 1) for x in block["bbox"])
                    width = bbox[2] - bbox[0]
                    height = bbox[3] - bbox[1]

                    # 过滤小碎片和幽灵图
                    if width < 50 or height < 50:
                        continue
                    if bbox in seen_bboxes:
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
                    except Exception as img_e:
                        full_content.append(f"\n[图片转换 PNG 失败: {str(img_e)}]\n")
                        continue

                    # 只有这张排版正常的图会被丢给 AI 识别
                    ai_result = parse_image(image_filename)

                    if ai_result["status"] == "success":
                        full_content.append(f"\n【图片 AI 识别内容】:\n{ai_result['data']}\n\n")
                    else:
                        full_content.append(f"\n【图片 AI 识别失败】: {ai_result['message']}\n\n")

        doc.close()

        return {
            "status": "success",
            "message": "PDF 解析完成",
            "data": "".join(full_content).strip()
        }

    except Exception as e:
        return {"status": "error", "message": f"PDF解析异常: {str(e)}", "data": ""}


if __name__ == "__main__":
    result = parse_smart_pdf("test_document.pdf")
    print(result["data"])