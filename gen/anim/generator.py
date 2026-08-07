"""
M5 - 动画/互动游戏生成器 (Owner: 赵钰洁)
负责接收 AI 生成的 HTML5 单文件代码，清洗并导出为 .html 文件
"""
import os
import re


class AnimGenerator:
    def __init__(self, session_id: str):
        self.session_id = session_id

    def _clean_html_content(self, raw_content: str) -> str:
        """
        非常关键的“提纯”函数：
        大模型（比如 DeepSeek）有时候很不听话，即使你让它直接输出代码，
        它还是会自作多情地加上 ```html 和 ``` 的 Markdown 标记。
        这个函数专门用来扒掉这些无用的外衣，防止浏览器解析报错。
        """
        # 如果包含 markdown 代码块标记，就用正则把它抠出来
        match = re.search(r'```(?:html)?\s*(.*?)\s*```', raw_content, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()

        # 如果没有标记，就原样返回去除首尾空格的代码
        return raw_content.strip()

    async def generate(self, instruction_data: dict, output_dir: str) -> str:
        """
        核心生成逻辑：
        提取 instruction_data 里的 HTML 代码，保存为单文件网页课件。
        """
        # 假设大模型生成的 HTML 代码存放在 html_code 这个 key 里
        raw_html = instruction_data.get("html_code", "")

        if not raw_html:
            # 如果没拿到数据，给一个默认的兜底页面，防止程序崩溃
            raw_html = """
            <!DOCTYPE html>
            <html>
            <head><meta charset="utf-8"><title>互动课件</title></head>
            <body><h2 style="text-align:center; margin-top:20%;">正在努力生成互动内容，请稍后再试...</h2></body>
            </html>
            """

        # 清洗代码
        clean_html = self._clean_html_content(raw_html)

        # 保存文件
        os.makedirs(output_dir, exist_ok=True)
        file_name = f"{self.session_id}_interactive.html"
        output_path = os.path.join(output_dir, file_name)

        # 必须指定 utf-8 编码，防止中文乱码
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(clean_html)

        return output_path


# 留给姜文杰 (编排器) 调用的主入口函数
async def gen_animation(instruction_data: dict, session_id: str, output_dir: str) -> str:
    """提供给外部的统一调用接口"""
    generator = AnimGenerator(session_id)
    file_path = await generator.generate(instruction_data, output_dir)
    return file_path
