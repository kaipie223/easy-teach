import os
import docx
from docx.table import Table
from docx.text.paragraph import Paragraph


def parse_comprehensive_word(file_path: str) -> dict:
    """
    【M2模块 - 多模态 Word 全景解析核心引擎】
    功能：提取文本、标题层级、基础样式（加粗/下划线）以及表格数据。
    预留：图片提取与多模态视觉大模型接口。
    """
    if not os.path.exists(file_path):
        return {"status": "error", "message": "文件不存在", "data": ""}

    try:
        doc = docx.Document(file_path)
        parsed_content = []

        # 核心技巧：按文档原始顺序，混合读取段落和表格
        # 这段代码能保证提取出来的内容，顺序和原文档一模一样
        for block in iter_block_items(doc):

            # 1. 如果遇到的是【普通段落文字】
            if isinstance(block, Paragraph):
                text = block.text.strip()
                if not text:
                    continue

                # 处理标题层级
                style_name = block.style.name
                if style_name.startswith('Heading'):
                    try:
                        level = int(style_name.split(' ')[-1])
                        parsed_content.append(f"{'#' * level} {text}")
                    except ValueError:
                        parsed_content.append(f"**{text}**")
                else:
                    # 侦测字体样式（比如加粗、下划线）
                    # 遍历段落里的每一个词块 (run)
                    formatted_text = ""
                    for run in block.runs:
                        run_text = run.text
                        if run.bold:
                            run_text = f"**{run_text}**"  # 转成 Markdown 加粗
                        if run.underline:
                            run_text = f"<u>{run_text}</u>"  # 转成 HTML 下划线
                        # 注：字体颜色(如标红)在 docx 中提取较复杂，这里做基础保留
                        formatted_text += run_text

                    parsed_content.append(formatted_text)

            # 2. 如果遇到的是【表格】
            elif isinstance(block, Table):
                parsed_content.append("\n【解析到一个表格】:")
                for row in block.rows:
                    row_data = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
                    # 用竖线把表格内容拼起来，符合大模型阅读习惯
                    parsed_content.append("| " + " | ".join(row_data) + " |")
                parsed_content.append("\n")

        # 3. TODO: 图片与复杂图形提取（预留区域）
        # 针对你提到的箭头、插图等复杂视觉元素，传统代码抓取极易丢失排版。
        # 完美的解决方案是：后续在这里加入逻辑，将复杂的 Word 页面转为图片，
        # 直接调用多模态大模型（Vision Model）进行视觉识别。
        image_analysis_result = "[预留接口：待接入多模态图片分析引擎]"

        final_text = '\n\n'.join(parsed_content)

        return {
            "status": "success",
            "message": "多模态文档初步解析完成",
            "data": {
                "text_and_tables": final_text,
                "visual_elements": image_analysis_result
            }
        }

    except Exception as e:
        return {"status": "error", "message": f"多模态解析失败: {str(e)}", "data": ""}


# ================= 辅助工具函数 =================
def iter_block_items(parent):
    """
    这是一个高级底层函数：能够按照 Word 里的真实排版顺序，依次吐出段落和表格。
    （如果不写这个函数，只能先读完所有文字，再读所有表格，顺序全乱了）
    """
    import docx.document
    import docx.oxml.table
    import docx.oxml.text.paragraph
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    if isinstance(parent, docx.document.Document):
        parent_elm = parent.element.body
    else:
        parent_elm = parent._element

    for child in parent_elm.iterchildren():
        if isinstance(child, docx.oxml.text.paragraph.CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, docx.oxml.table.CT_Tbl):
            yield Table(child, parent)
