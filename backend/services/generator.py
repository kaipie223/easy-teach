"""M5 — 课件生成器（PPT + Word + HTML 动画）"""


async def generate_pptx(intent: dict, rag_docs: list, references: list) -> str:
    """生成 PPT 课件，返回文件路径"""
    raise NotImplementedError("M5 PPT 生成 — 待赵钰洁实现")


async def generate_docx(intent: dict, rag_docs: list, references: list) -> str:
    """生成 Word 教案，返回文件路径"""
    raise NotImplementedError("M5 Word 生成 — 待赵钰洁实现")


async def generate_html(intent: dict, rag_docs: list, references: list) -> str:
    """生成 HTML 动画/互动游戏，返回文件路径"""
    raise NotImplementedError("M5 动画生成 — 待赵钰洁实现")
