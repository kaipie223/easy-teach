# -*- coding: utf-8 -*-
"""生成项目书「团队分工与协作」章节 docx。

数据来源：仓库代码内的 Owner 标注、git 提交记录与目录归属统计。
统计区间 2026-07-21 ~ 2026-09-23，已排除教材 PDF 等大体积二进制文件。
"""
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

OUT = r"C:\Users\kaipie\Desktop\easy-teach-app\docs\项目书-团队分工与协作.docx"

HEI = "黑体"
SONG = "宋体"
KAI = "楷体"

MORII = "姜文杰"  # git 账号 morii


def set_font(run, name=SONG, size=12, bold=False, color=None):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if color:
        run.font.color.rgb = color


def shade(cell, color="DCE6F1"):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), color)
    tcPr.append(shd)


def repeat_header(row):
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    trPr.append(el)


def body(doc, text, size=12, name=SONG, bold=False, indent=True, after=6, align=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.5
    if indent:
        p.paragraph_format.first_line_indent = Pt(size * 2)
    if align is not None:
        p.alignment = align
    set_font(p.add_run(text), name, size, bold)
    return p


def heading(doc, text, level=1):
    """level 1 -> 一、  2 -> （一）  3 -> 1."""
    sizes = {1: 15, 2: 13.5, 3: 12}
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12 if level == 1 else 8)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.5
    set_font(p.add_run(text), HEI, sizes[level], True)
    # 保留大纲级别，便于插入目录
    p.style = doc.styles["Normal"]
    pPr = p._p.get_or_add_pPr()
    lvl = OxmlElement("w:outlineLvl")
    lvl.set(qn("w:val"), str(level - 1))
    pPr.append(lvl)
    return p


def bullet(doc, label, text, size=12):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(5)
    p.paragraph_format.line_spacing = 1.5
    p.paragraph_format.left_indent = Pt(24)
    p.paragraph_format.first_line_indent = Pt(-0) if not label else None
    if label:
        set_font(p.add_run(label), HEI, size, True)
    set_font(p.add_run(text), SONG, size, False)
    return p


def make_table(doc, headers, rows, widths):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0]
    for i, h in enumerate(headers):
        cell = hdr.cells[i]
        cell.text = ""
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.space_before = Pt(2)
        set_font(p.add_run(h), HEI, 10.5, True)
        shade(cell)
    repeat_header(hdr)
    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.line_spacing = 1.15
            if i == 0 or len(str(v)) <= 4:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            set_font(p.add_run(str(v)), SONG, 10.5, False)
    for row in t.rows:
        for i, w in enumerate(widths):
            row.cells[i].width = Cm(w)
    return t


def caption(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(10)
    set_font(p.add_run(text), HEI, 10.5, True)


# ---------------------------------------------------------------- 文档
doc = Document()
sec = doc.sections[0]
sec.page_width = Cm(21)
sec.page_height = Cm(29.7)
sec.left_margin = sec.right_margin = Cm(2.8)
sec.top_margin = sec.bottom_margin = Cm(2.5)

normal = doc.styles["Normal"]
normal.font.name = SONG
normal.font.size = Pt(12)
normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), SONG)

# 标题
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(2)
set_font(p.add_run("Easy-Teach 多模态 AI 互动式教学智能体"), HEI, 16, True)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(16)
set_font(p.add_run("——团队分工与协作"), HEI, 13, True)

# ---------------------------------------------------------------- 一
heading(doc, "一、团队组成与分工原则", 1)
body(
    doc,
    "本项目团队由 5 名成员组成，覆盖 AI 算法、后端平台、文档解析与内容生成、"
    "交互前端四个技术方向。团队采用「模块归属 + 里程碑负责」的双维度分工方式："
    "纵向上，每位成员负责一个可独立交付的技术模块，对模块的接口、质量与进度负责；"
    "横向上，按 M0–M6 里程碑设置阶段主要负责人，牵头该阶段的联调与验收。"
    "跨模块接口由项目负责人牵头先固化契约，再由各成员并行开发，避免相互阻塞。",
)
caption(doc, "表 1  团队角色与分工总览")
make_table(
    doc,
    ["姓名", "项目角色", "负责模块", "主要交付物"],
    [
        ["陶克钦", "项目负责人\n技术负责人", "总体架构、前端工程、后端接口、视频解析",
         "项目架构与主干代码、Vue 3 前端工程、视频解析模块"],
        [MORII, "后端平台负责人\n质量保障", "服务端平台、数据层、管理端、部署与验收",
         "FastAPI 服务层、数据模型与迁移、管理端工作区、E2E 测试"],
        ["陈澜", "AI 能力负责人", "意图识别、RAG 检索、知识融合、语音",
         "RAG 混合检索链路、差异化追问策略、提示词库、教材知识库"],
        ["赵钰洁", "解析与生成引擎\n负责人", "文档解析、PPTX / DOCX / 互动内容生成",
         "五类文件解析引擎、三类课件生成器、PPT 样式与模板体系"],
        ["潘卓然", "交互前端负责人", "对话界面、文件上传、语音组件",
         "对话界面、文件上传组件、语音输入组件"],
    ],
    widths=[1.9, 2.6, 4.4, 6.3],
)

# ---------------------------------------------------------------- 二
heading(doc, "二、成员分工明细", 1)

heading(doc, "（一）陶克钦 —— 项目负责人 / 技术负责人", 2)
body(
    doc,
    "职责概述：负责项目总体架构设计与技术选型，划定模块边界与接口契约，把控整体进度，"
    "承担前后端集成与代码评审，并直接负责前端工程与视频解析模块的实现。",
)
body(doc, "主要工作：", bold=True, indent=False, after=4)
bullet(doc, "1. 项目架构与技术选型：", "确定 FastAPI + Vue 3 + PostgreSQL + Celery/Redis + DeepSeek 的技术栈，"
       "划定 backend/、ai/、gen/、video_parser/、frontend/ 的模块边界，建立统一的 /api/v1 接口规范与错误结构。")
bullet(doc, "2. 前端工程：", "主导 Vue 3 + TypeScript 工程重构，接入真实后端 API，打通"
       "「首页 → 对话 → 预览 → 下载」完整闭环；实现编辑器 AI 配图能力（单页生成、批量自动配图）、"
       "满版渐变排版，并修复导出环节缺陷。")
bullet(doc, "3. 后端接口：", "修复上传接口死锁问题并清理历史假数据；实现生成进度推送、SSE 对话流与资料解析接口。")
bullet(doc, "4. 视频解析模块：", "独立实现 video_parser/ 视频解析模块（24 个模块），涵盖 ffmpeg/ffprobe 环境预检与"
       "子进程超时控制、Whisper 模型加载失败降级、抽帧与关键帧策略、OCR 文本密度判定、"
       "中间表示（IR）构建、时间区间精修、教案环节时长归一、学生版内容裁剪与产物原子化落盘。")
bullet(doc, "5. 质量整改：", "主导视频解析模块阶段 1–6 共 40 余项缺陷修复（编号 S1.1–S6.16），"
       "覆盖超时与降级、证据时间戳真实性、抽帧容错、缓存键正确性、SDK 通路配置与产物一致性。")
bullet(doc, "6. 集成与评审：", "负责全部 8 次合并请求（#3–#10）的评审与主干合并，维护代码规范与提交纪律。")
bullet(doc, "交付物：", "项目总体架构与主干代码、Vue 3 前端工程、视频解析模块、质量整改记录。")

heading(doc, "（二）%s —— 后端平台负责人 / 质量保障" % MORII, 2)
body(
    doc,
    "职责概述：负责服务端平台架构、数据持久化与工程化基础设施，承担管理端开发、"
    "部署验收与全项目质量加固。",
)
body(doc, "主要工作：", bold=True, indent=False, after=4)
bullet(doc, "1. 工程化基础（M0）：", "统一使用 uv + pyproject.toml + uv.lock 管理 Python 依赖，"
       "建立 Alembic 数据库迁移体系与统一环境变量规范，固定接口前缀、错误结构与任务状态枚举。")
bullet(doc, "2. 账号与项目平台（M1–M2）：", "实现用户注册、登录、JWT 鉴权与角色权限；实现项目创建、"
       "列表、详情、软删除与恢复；将消息、资料、任务统一关联到用户与项目，完成数据持久化改造。")
bullet(doc, "3. 服务端架构与数据层：", "搭建 backend/services 服务层（57 个模块）、backend/routers 路由层与"
       "backend/models 数据模型，累计完成 12 个数据库迁移。")
bullet(doc, "4. 管理端与自动化测试：", "实现管理员工作区（用户管理、公共知识库管理、模型提供商与模型选择），"
       "并补充端到端（E2E）测试覆盖。")
bullet(doc, "5. 导出验收与质量加固（M6）：", "完成课件导出验收，主导全项目整改与加固，"
       "输出接口契约文档 docs/backend-api-contract.md。")
bullet(doc, "交付物：", "FastAPI 服务端平台与数据层、管理端工作区、12 个数据库迁移、E2E 测试、接口契约文档。")

heading(doc, "（三）陈澜 —— AI 能力负责人", 2)
body(
    doc,
    "职责概述：负责 AI 能力建设与知识库检索链路，覆盖意图识别、检索增强生成、知识融合与语音转写。",
)
body(doc, "主要工作：", bold=True, indent=False, after=4)
bullet(doc, "1. AI 模块框架：", "搭建意图理解（ai/intent）、RAG 检索（ai/rag）、知识融合（ai/fusion）、"
       "语音转写（ai/speech）与提示词工程（ai/prompts）五个子模块，形成完整 AI 能力底座。")
bullet(doc, "2. 混合检索（M3）：", "实现向量检索（权重 0.7）与关键词检索（权重 0.3）联合的混合检索策略，"
       "并将知识库扩展至 4 本教材，显著提升课程相关证据的召回质量。")
bullet(doc, "3. 提示词工程：", "设计需求澄清、知识融合、内容生成等场景的提示词模板，"
       "保证模型输出结构稳定、可解析、可校验。")
bullet(doc, "4. 效果调优：", "针对不同课程类型实现差异化追问策略，优化知识融合输出以充实教案内容；"
       "完成 AI 模块对新数据结构的适配，检索加载器对接解析模块产出。")
bullet(doc, "交付物：", "RAG 混合检索链路、意图识别与差异化追问策略、提示词库、4 本教材知识库。")

heading(doc, "（四）赵钰洁 —— 解析与生成引擎负责人", 2)
body(
    doc,
    "职责概述：负责多模态资料解析引擎与课件生成引擎，是「资料 → 蓝图 → 三类产物」"
    "核心链路的实现者。",
)
body(doc, "主要工作：", bold=True, indent=False, after=4)
bullet(doc, "1. 解析模块（M2）：", "实现 gen/parse 解析引擎，覆盖 PDF、Word、图片、音频、视频五类资料的"
       "统一解析，输出结构化内容，供证据链构建与向量检索使用。")
bullet(doc, "2. 生成引擎（M5）：", "实现三类课件生成器 —— gen/pptx PPT 课件生成器、gen/docx Word 教案生成器、"
       "gen/anim 动画与互动游戏生成器，由同一份教学蓝图驱动。")
bullet(doc, "3. 排版与模板体系：", "设计 PPT 样式与常量系统（gen/pptx/layout.py）及页面模板定义"
       "（gen/pptx/slides.py），保证生成产物的视觉一致性与可扩展性。")
bullet(doc, "4. 生成提示词：", "编写教案与课件生成所需的提示词模板（gen/prompts）。")
bullet(doc, "交付物：", "五类文件解析引擎、PPTX / DOCX / HTML5 互动内容三类生成器、PPT 样式与模板体系。")

heading(doc, "（五）潘卓然 —— 交互前端负责人", 2)
body(doc, "职责概述：负责教师端交互层实现，覆盖对话、资料上传与语音输入三类核心交互。")
body(doc, "主要工作：", bold=True, indent=False, after=4)
bullet(doc, "1. 需求共创界面（M1）：", "实现对话界面，支撑教师以自然语言表达教学需求并接收 AI 主动追问，"
       "完成需求共创环节的交互闭环。")
bullet(doc, "2. 资料上传交互（M2）：", "实现文件上传组件，支持多类型资料的选取、上传与状态反馈。")
bullet(doc, "3. 语音组件（M4）：", "实现语音输入与转写组件，将语音结果接入统一消息流水线。")
bullet(doc, "4. 基础架构融合：", "参与前端基础架构搭建与业务组件融合，为后续页面开发提供复用基础。")
bullet(doc, "交付物：", "对话界面、文件上传组件、语音输入组件。")

# ---------------------------------------------------------------- 三
heading(doc, "三、里程碑责任分配", 1)
body(
    doc,
    "项目按 M0–M6 七个里程碑推进，每个里程碑设一名主要负责人牵头该阶段的开发、联调与验收，"
    "并配置协作成员参与。具体责任分配如下。",
)
caption(doc, "表 2  M0–M6 里程碑责任矩阵")
make_table(
    doc,
    ["里程碑", "阶段目标", "主要负责人", "协作成员"],
    [
        ["M0", "基线与工程化：依赖管理、迁移体系、接口规范", MORII, "陶克钦"],
        ["M1", "账号、项目与数据持久化", MORII, "潘卓然、陶克钦"],
        ["M2", "需求共创与多模态资料解析", "赵钰洁", "潘卓然、陈澜、陶克钦"],
        ["M3", "资料解析、知识库与检索", "陈澜", "陶克钦"],
        ["M4", "教学蓝图与课件生成", "赵钰洁", "陈澜、陶克钦"],
        ["M5", "局部修改、版本管理与导出", "赵钰洁", "陶克钦"],
        ["M6", "质量、部署与验收", MORII, "陶克钦、陈澜"],
    ],
    widths=[1.8, 7.2, 3.0, 3.2],
)

# ---------------------------------------------------------------- 四
heading(doc, "四、协作机制", 1)
bullet(doc, "1. 分支管理与代码评审：", "主干 main 分支保持可发布状态，功能开发在特性分支进行，"
       "统一通过合并请求（Pull Request）提交评审，由项目负责人评审通过后合入主干；"
       "全项目累计完成 8 次合并请求（#3–#10）。")
bullet(doc, "2. 接口契约先行的并行开发：", "跨模块接口先固化 Schema 与 API 文档，再并行开发，"
       "减少前后端与模块之间的返工；接口变更必须同步更新后端 Schema、接口文档与契约测试。")
bullet(doc, "3. 里程碑验收制度：", "每个里程碑完成后运行对应测试并记录结果，达标后方可进入下一阶段；"
       "长任务统一具备状态、进度、错误、重试与幂等策略。")
bullet(doc, "4. 文档与知识沉淀：", "维护开发交接文档、接口契约文档、整改计划与验收矩阵，"
       "保证成员之间上下文可传递、责任可追溯。")

# ---------------------------------------------------------------- 五
heading(doc, "五、工作量统计", 1)
body(
    doc,
    "下表统计各成员在 2026 年 7 月 21 日至 9 月 23 日期间的代码贡献情况。"
    "统计已排除教材 PDF 等大体积二进制文件，仅计入源码与文档的实际改动。",
)
caption(doc, "表 3  成员代码贡献统计")
make_table(
    doc,
    ["成员", "文件变更次数", "新增代码行", "删除代码行", "主要涉及范围"],
    [
        [MORII, "355", "36,335", "5,091", "backend/services、backend/routers、backend/models、backend/core、alembic、frontend/src"],
        ["陶克钦", "247", "20,995", "1,173", "backend/services、backend/routers、frontend/src、video_parser、docs"],
        ["潘卓然", "32", "5,647", "87", "frontend/src"],
        ["陈澜", "47", "1,516", "484", "ai/rag、ai/prompts、ai/intent、ai/fusion、ai/speech"],
        ["赵钰洁", "24", "1,177", "203", "gen/parse、gen/pptx、gen/docx、gen/anim"],
        ["合计", "705", "65,670", "7,038", "—"],
    ],
    widths=[1.9, 2.3, 2.2, 2.2, 6.6],
)

doc.save(OUT)
print("saved:", OUT)
