"""One vocabulary for "how far along is this job, and what is it doing right now".

Every long-running pipeline — blueprint build, courseware generation, AI revision,
export render, material analysis — reports through here instead of each caller
picking its own percentage. Two things follow:

- a stage carries its own completion percentage, so progress is monotonic and a
  caller can no longer invent a number that jumps backwards;
- the Chinese wording travels with the stage key, so the client renders what the
  server says instead of keeping a second copy of the text. Two copies of the same
  table always drift eventually.

Percentages mark *finished milestones*, not elapsed time. Rendering a lesson
document may take a tenth of the wall clock while occupying a tenth of the bar,
and building the blueprint may occupy a fifth while taking the longest. That is
deliberate: a milestone bar never stalls at 90% or walks backwards, which a time
estimate cannot promise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    percent: int


# ── 课件生成任务 ────────────────────────────────────────

GENERATION_STAGES: tuple[Stage, ...] = (
    Stage("brief", "校验需求确认单", 8),
    Stage("search", "检索证据与参考资料", 20),
    Stage("parse", "解析参考资料", 32),
    Stage("plan", "AI 正在生成教学蓝图", 46),
    Stage("quality", "校验成果质量", 66),
    # 逐页生成插图，是本条链路上第二慢的一步，单独占一个阶段才看得见进展
    Stage("illustrate", "为每页生成配图", 70),
    Stage("pptx", "渲染演示文稿", 78),
    Stage("docx", "渲染教学教案", 88),
    Stage("html", "渲染互动练习", 95),
    Stage("persist", "登记生成产物", 99),
)

# 构建蓝图是整条链路里最慢的一步（多次模型往返），如果只在进入和离开时报 46 与
# 66，进度条会在这一段长时间停住。把它的子阶段映射成 plan 步骤内的文案与百分比，
# 于是"AI 现在在做什么"在最慢的地方反而是最清楚的。
PLAN_DETAILS: dict[str, tuple[str, int]] = {
    "evidence": ("整理证据与参考资料", 48),
    "reused": ("复用已有教学蓝图", 64),
    "template": ("按基础模板生成蓝图", 52),
    "generate": ("AI 正在生成教学蓝图", 52),
    "repair": ("AI 正在修复结构问题", 57),
    "review": ("AI 正在审校教学事实", 61),
    "review_repair": ("AI 正在修复审校结果", 64),
    "persist": ("保存蓝图与成果版本", 65),
}


# ── 教学蓝图 ────────────────────────────────────────────

PLAN_STAGES: tuple[Stage, ...] = (
    Stage("brief", "校验需求确认单", 5),
    Stage("evidence", "整理证据与参考资料", 15),
    Stage("generate", "AI 正在生成教学蓝图", 45),
    Stage("review", "AI 正在审校教学事实", 80),
    Stage("persist", "保存蓝图与成果版本", 97),
)

# 分支阶段只用来改写"当前在做什么"，不进步骤条：目录里出现一个永远不会执行的
# 步骤，比没有步骤条更让人困惑。
PLAN_BRANCHES: dict[str, tuple[str, int]] = {
    "reused": ("复用已有教学蓝图", 100),
    "template": ("按基础模板生成蓝图", 45),
    "repair": ("AI 正在修复结构问题", 62),
    "review_repair": ("AI 正在修复审校结果", 90),
}


# ── AI 局部重生成 ───────────────────────────────────────

REVISION_STAGES: tuple[Stage, ...] = (
    Stage("generate", "AI 正在重生成目标内容", 40),
    Stage("persist", "保存新的成果版本", 95),
)

# 沿用界面上"AI 重生成"的说法，避免同一动作在不同位置换措辞
REVISION_BRANCHES: dict[str, tuple[str, int]] = {
    "repair": ("AI 正在修复结构问题", 72),
}


# ── 导出 ────────────────────────────────────────────────

# 每条导出记录只负责一个格式，所以这里没有步骤条，只有进度条与文案。参考
# `export_stage_label`：渲染文案要把格式名补进去。
EXPORT_STAGES: tuple[Stage, ...] = (
    Stage("render", "正在渲染", 55),
    Stage("save", "保存导出文件", 90),
)

EXPORT_FORMAT_LABELS = {
    "pptx": "演示文稿",
    "docx": "教学教案",
    "pdf": "打印版",
    "html": "互动练习",
}


# ── 资料解析 ────────────────────────────────────────────

# 按资料类型给不同的阶段表，步骤条才不会列出一个永远不会执行的步骤：图片多一步
# 视觉识别，视频多一步转录解析（也是这里唯一可能跑几分钟的一步）。
_MATERIAL_DOCUMENT: tuple[Stage, ...] = (
    Stage("extract", "提取文件内容", 25),
    Stage("index", "建立检索索引", 92),
)
_MATERIAL_IMAGE: tuple[Stage, ...] = (
    Stage("extract", "提取文件内容", 25),
    Stage("vision", "AI 正在识别图片内容", 70),
    Stage("index", "建立检索索引", 92),
)
_MATERIAL_VIDEO: tuple[Stage, ...] = (
    Stage("extract", "提取文件内容", 15),
    Stage("parse", "正在转录与解析视频", 75),
    Stage("index", "建立检索索引", 92),
)


def _plain(value: object) -> object:
    """Unwrap a str-enum into its value.

    `class FileType(str, Enum)` compares equal to its value but hashes on the member
    *name*, so a dict lookup keyed by the raw string silently misses. Always
    unwrapping here keeps that trap out of every caller.
    """
    return getattr(value, "value", value)


def material_stages(file_type: object) -> tuple[Stage, ...]:
    name = _plain(file_type)
    if name == "image":
        return _MATERIAL_IMAGE
    if name == "video":
        return _MATERIAL_VIDEO
    return _MATERIAL_DOCUMENT


# ── 查询 ────────────────────────────────────────────────


def stage_table(
    stages: Iterable[Stage],
    branches: dict[str, tuple[str, int]] | None = None,
) -> dict[str, tuple[str, int]]:
    """key → (label, percent), folding the workflow's branch stages in."""
    table = {stage.key: (stage.label, stage.percent) for stage in stages}
    if branches:
        table.update(branches)
    return table


def find(stages: Iterable[Stage], key: str | None) -> Stage | None:
    for stage in stages:
        if stage.key == key:
            return stage
    return None


def label_of(
    stages: Iterable[Stage],
    key: str | None,
    branches: dict[str, tuple[str, int]] | None = None,
) -> str | None:
    if key is None:
        return None
    table = stage_table(stages, branches)
    entry = table.get(key)
    return entry[0] if entry else None


def percent_of(
    stages: Iterable[Stage],
    key: str | None,
    branches: dict[str, tuple[str, int]] | None = None,
) -> int:
    if key is None:
        return 0
    table = stage_table(stages, branches)
    entry = table.get(key)
    return entry[1] if entry else 0


def manifest(stages: Iterable[Stage]) -> list[dict[str, object]]:
    """The ordered list the client turns into a stepper."""
    return [
        {"key": stage.key, "label": stage.label, "percent": stage.percent}
        for stage in stages
    ]


def frame(
    stages: Iterable[Stage],
    key: str,
    *,
    branches: dict[str, tuple[str, int]] | None = None,
    include_manifest: bool = True,
) -> dict[str, object]:
    """SSE payload for a stage change: the key, its wording, its percent."""
    payload: dict[str, object] = {
        "stage": key,
        "stage_label": label_of(stages, key, branches),
        "percent": percent_of(stages, key, branches),
    }
    if include_manifest:
        payload["stages"] = manifest(stages)
    return payload


def material_ai_stage(file_type: object) -> str | None:
    """The model-backed step of a material's parsing, when it has one.

    对图片和视频，"提取文件内容"只是读元信息，真正耗时的是它后面的模型调用，
    所以调用方要在开始等待之前就把阶段推到这一步，否则进度条会一直停在 25%。
    """
    name = _plain(file_type)
    if name == "image":
        return "vision"
    if name == "video":
        return "parse"
    return None


def material_frame(file_type: object, key: str) -> dict[str, object]:
    """Stage fields for a material, including the stepper for its own file type."""
    stages = material_stages(file_type)
    return {
        "stage": key,
        "stage_label": label_of(stages, key),
        "stage_percent": percent_of(stages, key),
        "stages": manifest(stages),
    }


def export_stage_label(export_format: object, key: str | None) -> str | None:
    """Rendering wording for one export record, with its format folded in."""
    if key is None:
        return None
    name = _plain(export_format)
    if key == "render":
        return f"正在渲染{EXPORT_FORMAT_LABELS.get(name, name)}"
    return label_of(EXPORT_STAGES, key)
