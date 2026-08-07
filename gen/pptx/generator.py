"""
M5 - PPT 课件生成器 (Owner: 赵钰洁)
负责接收 AI 生成的大纲，组装并导出 .pptx 文件
"""
import os
from pptx import Presentation


# 等你写完下面这两个文件，就可以取消注释了
# from .layout import get_theme_colors, get_font_settings
# from .slides import create_cover_slide, create_content_slide

class PPTGenerator:
    def __init__(self, session_id: str):
        self.session_id = session_id
        # 初始化一个空白的 PPT 对象
        self.prs = Presentation()

    async def generate(self, instruction_data: dict, output_dir: str) -> str:
        """
        核心生成逻辑：
        接收陈澜那边传过来的教学大纲 (instruction_data)，生成 PPT
        """
        # 1. 提取大纲数据 (后续根据真实的 instruction_data 结构调整)
        title = instruction_data.get("title", "未命名课件")
        slides_data = instruction_data.get("slides", [])

        # 2. 生成封面 (临时占位，后续调你的 slides.py)
        blank_slide_layout = self.prs.slide_layouts[0]
        slide = self.prs.slides.add_slide(blank_slide_layout)
        title_shape = slide.shapes.title
        if title_shape:
            title_shape.text = title

        # 3. 循环生成内容页 (临时占位)
        for page in slides_data:
            bullet_slide_layout = self.prs.slide_layouts[1]
            content_slide = self.prs.slides.add_slide(bullet_slide_layout)
            shapes = content_slide.shapes
            if shapes.title:
                shapes.title.text = page.get("heading", "无标题")

        # 4. 保存文件
        os.makedirs(output_dir, exist_ok=True)
        file_name = f"{self.session_id}_presentation.pptx"
        output_path = os.path.join(output_dir, file_name)

        self.prs.save(output_path)
        return output_path


# 留给姜文杰 (编排器) 调用的主入口函数
async def gen_ppt(instruction_data: dict, session_id: str, output_dir: str) -> str:
    """提供给外部的统一调用接口"""
    generator = PPTGenerator(session_id)
    file_path = await generator.generate(instruction_data, output_dir)
    return file_path
