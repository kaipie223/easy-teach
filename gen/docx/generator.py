"""
M5 - Word 教案生成器 (Owner: 赵钰洁)
负责接收 AI 生成的教案内容，组装并导出为 6 大板块的标准 .docx 教案
"""
import os
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH


class DocxGenerator:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.doc = Document()
        self._setup_styles()

    def _setup_styles(self):
        """初始化 Word 文档的全局样式，确保老师打开后看着舒服"""
        style = self.doc.styles['Normal']
        font = style.font
        font.name = '宋体'  # 教案最标准的公文/教学用字
        font.size = Pt(12)  # 小四号字

    async def generate(self, instruction_data: dict, output_dir: str) -> str:
        """
        核心生成逻辑：
        根据大模型传回来的 instruction_data，按 6 大板块组装教案
        """
        # 1. 插入大标题并居中
        title = instruction_data.get("title", "未命名教案")
        heading = self.doc.add_heading(title, level=0)
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # 提取 AI 生成的教案各板块数据
        sections = instruction_data.get("doc_sections", {})

        # 2. 定义老师最熟悉的 6 大教学板块标准结构
        plate_names = [
            ("objective", "一、 教学目标 (三维目标)"),
            ("key_points", "二、 教学重难点"),
            ("methods", "三、 教学方法与教具准备"),
            ("process", "四、 教学过程 (核心设计)"),
            ("board_design", "五、 板书设计"),
            ("homework", "六、 课后作业与教学反思")
        ]

        # 3. 循环遍历，把内容写入文档
        for key, title_text in plate_names:
            # 添加板块标题（一级标题）
            self.doc.add_heading(title_text, level=1)

            # 获取内容，如果没有则填入默认占位符
            content = sections.get(key, "待 AI 生成内容...")

            # AI 传过来的可能是分段的列表，也可能是一大段纯文本，做个兼容处理
            if isinstance(content, list):
                for paragraph in content:
                    self.doc.add_paragraph(paragraph)
            else:
                self.doc.add_paragraph(str(content))

        # 4. 保存文件
        os.makedirs(output_dir, exist_ok=True)
        file_name = f"{self.session_id}_lesson_plan.docx"
        output_path = os.path.join(output_dir, file_name)

        self.doc.save(output_path)
        return output_path


# 留给姜文杰 (编排器) 调用的主入口函数
async def gen_doc(instruction_data: dict, session_id: str, output_dir: str) -> str:
    """提供给外部的统一调用接口"""
    generator = DocxGenerator(session_id)
    file_path = await generator.generate(instruction_data, output_dir)
    return file_path
