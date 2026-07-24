import os
import fitz
# 从你的 image 模块导入咱们刚写好的内存接收函数
from gen.parse.image import parse_image_from_bytes


def parse_smart_pdf(file_path: str) -> dict:
    if not os.path.exists(file_path):
        return {"status": "error", "message": "文件不存在", "data": ""}

    try:
        doc = fitz.open(file_path)
        full_text_blocks = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text().strip()

            full_text_blocks.append(f"\n\n=========== 第 {page_num + 1} 页 ===========\n")

            # 1. 尝试读纯文本
            if text:
                full_text_blocks.append(text)

            # 2. 智能分流：如果是扫描版大图片
            else:
                image_list = page.get_images()
                if image_list:
                    # 获取真实的图片数据流 (一堆计算机才懂的字节)
                    xref = image_list[0][0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]

                    # 【高能预警：这里直接发生了无缝交接！】
                    # 不存任何文件，直接把数据流砸给 image.py 里面的函数
                    image_result = parse_image_from_bytes(image_bytes)

                    if image_result["status"] == "success":
                        full_text_blocks.append(image_result['data'])
                    else:
                        full_text_blocks.append("【扫描版识别失败】")
                else:
                    full_text_blocks.append("【本页为空白页】")

        doc.close()
        return {"status": "success", "message": "PDF 智能解析完成", "data": "".join(full_text_blocks).strip()}

    except Exception as e:
        return {"status": "error", "message": f"PDF解析异常: {str(e)}", "data": ""}