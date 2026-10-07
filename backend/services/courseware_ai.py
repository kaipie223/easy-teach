"""DeepSeek adapter for validated, evidence-aware teaching blueprints."""

from __future__ import annotations

import json
import logging
import math
import re
import threading
from collections import Counter, defaultdict, deque
from collections.abc import Collection, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from pydantic import ValidationError

from backend.config import settings
from backend.schemas import (
    MAX_BULLETS_PER_SLIDE_LIMIT,
    MAX_SLIDES_PER_DECK,
    MIN_SLIDES_PER_DECK,
    SCENE_ENTITY_KINDS,
    SCENE_KIND_EXPRESSION_PROPS,
    SCENE_KIND_REQUIRED_PROPS,
    SCENE_MAX_ENTITIES,
    SCENE_MAX_PATH_SAMPLES,
    SCENE_PALETTE,
    SCENE_RESERVED_NAMES,
    SCENE_TEXT_MAX_LENGTH,
    CoursewarePlanSpec,
    EvidenceRef,
    RENDERED_TOOL_ENGINES,
    TOOL_ENGINES,
    limit_emphasis_to_bullets,
)
from backend.services.ai_completion import json_completion
from backend.services.brief import normalize_content
from backend.services.expressions import ExpressionError, compile_expression, referenced_names

logger = logging.getLogger(__name__)

from backend.services.prompt_library import (
    build_fill_slides_prompt,
    build_fill_teaching_prompt,
    build_generation_prompt,
    build_review_prompt,
    build_skeleton_prompt,
)

MODEL_NAME = settings.deepseek_model
# 提示词从"控篇幅"改成"质量优先"后必须升版本：生成质量回溯全靠它，不升版本就
# 分不清一份蓝图是旧规则还是新规则的产物。
PROMPT_VERSION = "courseware-plan-v24-scene"




class CoursewareAIError(RuntimeError):
    def __init__(self, message: str, *, code: str, recoverable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.recoverable = recoverable


@dataclass(frozen=True)
class CoursewareAIResult:
    spec: CoursewarePlanSpec
    model_name: str
    prompt_version: str
    usage: dict[str, int]


def _extract_json(text: str) -> Any:
    raw = (text or "").strip()
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL | re.IGNORECASE)
    if match:
        raw = match.group(1).strip()
    return json.loads(raw)


def _completion(
    client: OpenAI,
    messages: list[dict[str, str]],
    *,
    thinking: bool | None = None,
):
    """一次对话补全：输出预算按配置给足，质量优先。

    预算以前写死 8000，整册内容（slides + 教案 + 互动 + 四类成果设定）共用这一份
    预算，模型只能把每条都写短——这正是"内容单薄"的来源。具体预算与降级策略见
    ``backend.services.ai_completion``：被服务端拒绝时自动降级，不会因为调大而打死。

    ``thinking`` 由调用方按教师的"深度思考"开关传入；None 表示按服务端配置。
    """
    return json_completion(client, messages, model=MODEL_NAME, thinking=thinking)


def _make_completer(client: OpenAI, thinking: bool | None):
    """把 ``thinking`` 绑定进补全调用，避免在每个调用点重复传参。

    一次生成有十来个补全调用（骨架、教案、每页讲稿、修复、审校…）。逐个传参迟早
    会漏掉一处，而漏掉的那一处往往正好是修复或审校步骤——最难发现、影响最大。
    """

    def complete(messages: list[dict[str, str]]):
        return _completion(client, messages, thinking=thinking)

    return complete


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def _finish_reason(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    return str(getattr(choices[0], "finish_reason", "") or "") if choices else ""


def _add_usage(total: dict[str, int], response: Any) -> None:
    current = _usage(response)
    for key in total:
        total[key] += current[key]


def _bind_refs(index: int, refs: list[EvidenceRef]) -> list[EvidenceRef]:
    if not refs:
        return []
    selected = [refs[index % len(refs)]]
    if len(refs) > 1:
        selected.append(refs[(index + 1) % len(refs)])
    return selected


def _normalize_interaction_data(item: dict[str, Any], *, course_context: str) -> None:
    raw_items = item.get("items")
    raw_groups = item.get("answer_groups")
    if not isinstance(raw_items, list) or not isinstance(raw_groups, dict):
        return

    if (
        "光合作用" in course_context
        and item.get("interaction_type") == "quiz"
        and "暗反应" in str(item.get("prompt") or "")
        and "黑暗" in str(item.get("prompt") or "")
    ):
        item["prompt"] = (
            "在持续黑暗、光反应不再提供 ATP 和 NADPH 的条件下，"
            "暗反应能否长期持续？"
        )
        item["items"] = ["能", "不能"]
        item["answer_groups"] = {"正确": ["不能"]}
        item["explanation"] = (
            "暗反应不直接利用光，但依赖光反应提供的 ATP 和 NADPH；"
            "已有物质耗尽后不能长期持续。"
        )
        raw_items = item["items"]
        raw_groups = item["answer_groups"]

    if item.get("interaction_type") == "ordering":
        totals = Counter(raw_items)
        seen: dict[Any, int] = defaultdict(int)
        labels_by_value: dict[Any, deque[Any]] = defaultdict(deque)
        unique_items = []
        for value in raw_items:
            seen[value] += 1
            label = f"{value}（{seen[value]}）" if totals[value] > 1 else value
            unique_items.append(label)
            labels_by_value[value].append(label)
        item["items"] = unique_items
        normalized_groups = {}
        for key, values in raw_groups.items():
            if not isinstance(values, list):
                normalized_groups[key] = values
                continue
            normalized_values = []
            for value in values:
                if labels_by_value[value]:
                    normalized_values.append(labels_by_value[value].popleft())
                else:
                    normalized_values.append(value)
            normalized_groups[key] = normalized_values
        item["answer_groups"] = normalized_groups
        if len(normalized_groups) > 1 and all(
            str(label).isdigit() and isinstance(values, list) and len(values) == 1
            for label, values in normalized_groups.items()
        ):
            ordered_values = [
                normalized_groups[label][0]
                for label in sorted(normalized_groups, key=lambda value: int(str(value)))
            ]
            item["answer_groups"] = {"正确顺序": ordered_values}
        return

    item["items"] = list(dict.fromkeys(raw_items))
    item["answer_groups"] = {
        key: list(dict.fromkeys(values)) if isinstance(values, list) else values
        for key, values in raw_groups.items()
    }
    if item.get("interaction_type") == "quiz" and len(item["answer_groups"]) > 1:
        correct_labels = {
            "正确",
            "正确答案",
            "答案",
            "correct",
            "correct answer",
        }
        matching_labels = [
            label
            for label in item["answer_groups"]
            if str(label).strip().lower() in correct_labels
        ]
        if len(matching_labels) == 1:
            correct_label = matching_labels[0]
            item["answer_groups"] = {
                correct_label: item["answer_groups"][correct_label]
            }
    if item.get("interaction_type") != "classification" or "节奏" not in course_context:
        if item.get("interaction_type") != "classification":
            return
    if "光合作用" in course_context and "暗反应产物" in item["answer_groups"]:
        related = item["answer_groups"].pop("暗反应产物")
        item["answer_groups"]["暗反应相关物质"] = related
        item["prompt"] = str(item.get("prompt") or "").replace(
            "暗反应产物", "暗反应相关物质"
        )
        item["explanation"] = str(item.get("explanation") or "").replace(
            "暗反应产生C₃、C₅和葡萄糖",
            "C₃和C₅是暗反应循环中的中间物质，糖类是形成的有机物",
        )
    if "浮力" in course_context:
        replacements = {
            "物体体积": "物体体积（液体密度和排开体积相同时）",
            "物体的形状": "物体的形状（液体密度和排开体积相同时）",
        }
        for old, new in replacements.items():
            item["items"] = [new if value == old else value for value in item["items"]]
            for label, values in item["answer_groups"].items():
                item["answer_groups"][label] = [
                    new if value == old else value for value in values
                ]
    if "节奏" not in course_context:
        return

    mixed_items = [
        value
        for value in item["items"]
        if isinstance(value, str)
        and (("ta" in value and "ti-ti" in value) or ("走" in value and "跑跑" in value))
    ]
    if not mixed_items:
        return
    groups = item["answer_groups"]
    mixed_label = next(
        (label for label in groups if "混合" in str(label) or "组合" in str(label)),
        "混合节奏",
    )
    for label, values in groups.items():
        if isinstance(values, list):
            groups[label] = [value for value in values if value not in mixed_items]
    groups.setdefault(mixed_label, [])
    groups[mixed_label].extend(value for value in mixed_items if value not in groups[mixed_label])


def _reconcile_quiz_answers(item: dict[str, Any]) -> None:
    items = item.get("items")
    groups = item.get("answer_groups")
    if not isinstance(items, list) or not isinstance(groups, dict):
        return

    known_items = set(items)
    valid_groups = {
        str(label): [value for value in values if value in known_items]
        for label, values in groups.items()
        if isinstance(values, list)
    }
    valid_groups = {label: values for label, values in valid_groups.items() if values}
    if len(valid_groups) <= 1:
        item["answer_groups"] = valid_groups
        return

    def is_negative_label(label: str) -> bool:
        normalized = label.strip().lower()
        return any(
            marker in normalized
            for marker in ("错误", "不正确", "干扰", "incorrect", "wrong", "distractor")
        )

    def is_positive_label(label: str) -> bool:
        normalized = label.strip().lower()
        return not is_negative_label(label) and any(
            marker in normalized
            for marker in ("正确", "答案", "参考", "correct", "answer", "solution")
        )

    positive_answers = list(
        dict.fromkeys(
            value
            for label, values in valid_groups.items()
            if is_positive_label(label)
            for value in values
        )
    )
    if positive_answers:
        item["answer_groups"] = {"正确答案": positive_answers}
        return

    negative_answers = {
        value
        for label, values in valid_groups.items()
        if is_negative_label(label)
        for value in values
    }
    inferred_answers = [value for value in items if value not in negative_answers]
    if negative_answers and inferred_answers:
        item["answer_groups"] = {"正确答案": inferred_answers}
        return

    item["answer_groups"] = valid_groups


def _reconcile_interaction_assignments(item: dict[str, Any]) -> None:
    """Keep only answerable cards and reconcile answer assignments."""
    if item.get("interaction_type") == "quiz":
        _reconcile_quiz_answers(item)
        return
    items = item.get("items")
    groups = item.get("answer_groups")
    if not isinstance(items, list) or not isinstance(groups, dict):
        return

    known_items = set(items)
    assigned: set[Any] = set()
    normalized_groups: dict[str, list[Any]] = {}
    ordered_answers: list[Any] = []
    for label, values in groups.items():
        if not isinstance(values, list):
            continue
        normalized_values = []
        for value in values:
            if value not in known_items or value in assigned:
                continue
            assigned.add(value)
            normalized_values.append(value)
            ordered_answers.append(value)
        if normalized_values:
            normalized_groups[str(label)] = normalized_values

    if not assigned:
        item["answer_groups"] = {}
        return
    item["items"] = [value for value in items if value in assigned]
    if item.get("interaction_type") == "ordering":
        item["answer_groups"] = {"正确顺序": ordered_answers}
    else:
        item["answer_groups"] = normalized_groups


def _normalize_section_durations(
    sections: list[Any],
    *,
    target_minutes: int,
) -> None:
    """Scale model-provided section weights to an exact course duration."""
    valid_sections = [section for section in sections if isinstance(section, dict)]
    if not valid_sections or target_minutes < len(valid_sections):
        return
    weights = []
    for section in valid_sections:
        try:
            weights.append(max(1, int(section.get("duration_minutes") or 1)))
        except (TypeError, ValueError):
            weights.append(1)
    if sum(weights) == target_minutes:
        return

    distributable = target_minutes - len(valid_sections)
    total_weight = sum(weights)
    exact_shares = [distributable * weight / total_weight for weight in weights]
    allocations = [int(share) for share in exact_shares]
    remainder = distributable - sum(allocations)
    order = sorted(
        range(len(valid_sections)),
        key=lambda index: (exact_shares[index] - allocations[index], weights[index]),
        reverse=True,
    )
    for index in order[:remainder]:
        allocations[index] += 1
    for section, allocation in zip(valid_sections, allocations, strict=True):
        section["duration_minutes"] = allocation + 1


def _sanitize_slide_images(slides: Any, allowed_image_ids: Collection[str]) -> int:
    """Drop picture references the model invented or copied from elsewhere.

    The prompt lists the IDs the model may use, but a model that ignores
    instructions must not be able to point a slide at an arbitrary material:
    pictures are private uploads, so an unlisted ID is removed here rather than
    resolved later. An unknown placement only falls back to the default, because
    failing a whole blueprint over a cosmetic field helps nobody.
    """
    if not isinstance(slides, list):
        return 0
    dropped = 0
    for slide in slides:
        if not isinstance(slide, dict):
            continue
        image = slide.get("image")
        if image is None:
            continue
        if not isinstance(image, dict) or str(image.get("material_id") or "") not in allowed_image_ids:
            slide["image"] = None
            dropped += 1
            continue
        if image.get("placement") not in {"right", "full", "background"}:
            image["placement"] = "right"
    return dropped


def _normalize_and_validate(
    raw: Any,
    *,
    brief_content: dict[str, Any],
    evidence_refs: list[EvidenceRef],
    allowed_image_ids: Collection[str] = frozenset(),
) -> CoursewarePlanSpec:
    if not isinstance(raw, dict):
        raise TypeError("courseware response must be a JSON object")
    if not isinstance(raw.get("slides"), list):
        nested_candidates = [
            value
            for value in raw.values()
            if isinstance(value, dict) and isinstance(value.get("slides"), list)
        ]
        if len(nested_candidates) == 1:
            raw = nested_candidates[0]

    content = normalize_content(brief_content)
    data = dict(raw)
    _coerce_model_shapes(data)
    data.update(
        {
            "target_audience": content["target_audience"],
            "duration_minutes": content["duration_minutes"],
            "teaching_goal": content["teaching_goal"],
            "knowledge_points": content["knowledge_points"],
            "logic_flow": content["logic_flow"],
            "teaching_focus": content["teaching_focus"],
            "teaching_difficulties": content["teaching_difficulties"],
            "style_preference": content["style_preference"],
            "evidence_refs": [item.model_dump(mode="json") for item in evidence_refs],
        }
    )
    data["title"] = str(data.get("title") or content["teaching_goal"]).strip()

    slides = data.get("slides")
    if isinstance(slides, list):
        dropped_images = _sanitize_slide_images(slides, allowed_image_ids)
        if dropped_images:
            logger.warning(
                "Dropped %s slide picture reference(s) that were not offered to the model",
                dropped_images,
            )
    regional_context = " ".join(
        str(value)
        for value in (data.get("title", ""), content.get("teaching_goal", ""))
    )
    if "垃圾分类" in regional_context and isinstance(slides, list) and slides:
        serialized_slides = json.dumps(slides, ensure_ascii=False)
        regional_markers = ("授课地", "当地现行标准", "所在城市", "本市标准")
        if not any(marker in serialized_slides for marker in regional_markers):
            first_bullets = slides[0].get("bullets") if isinstance(slides[0], dict) else None
            if isinstance(first_bullets, list) and first_bullets:
                first_bullets[0] = (
                    "口径说明：以下为常见四分类示例，具体投放规则以授课地现行标准为准，"
                    f"教师课前核验；{first_bullets[0]}"
                )
        if not evidence_refs:
            for slide in slides:
                bullets = slide.get("bullets") if isinstance(slide, dict) else None
                if not isinstance(bullets, list):
                    continue
                for bullet_index, bullet in enumerate(bullets):
                    if isinstance(bullet, str) and re.search(r"\d+\s*吨.*\d+", bullet):
                        bullets[bullet_index] = (
                            "资源回收可减少原生资源消耗；具体数据应引用可靠来源并结合当地资料。"
                        )
    if "颜色" in regional_context and isinstance(slides, list) and slides:
        serialized_slides = json.dumps(slides, ensure_ascii=False)
        if "红、黄、蓝" in serialized_slides and not any(
            marker in serialized_slides for marker in ("颜料", "减色")
        ):
            first_bullets = slides[0].get("bullets") if isinstance(slides[0], dict) else None
            if isinstance(first_bullets, list) and first_bullets:
                first_bullets[0] = (
                    "语境说明：本课红、黄、蓝三原色指颜料混色；"
                    f"{first_bullets[0]}"
                )
    if "化学平衡" in regional_context and isinstance(slides, list):
        wrong_equation = re.compile(
            r"FeCl[₃3]\s*\+\s*3KSCN\s*⇌\s*Fe\(SCN\)[₃3]\s*\+\s*3KCl"
        )
        for slide in slides:
            bullets = slide.get("bullets") if isinstance(slide, dict) else None
            if isinstance(bullets, list):
                slide["bullets"] = [
                    wrong_equation.sub("Fe³⁺ + SCN⁻ ⇌ FeSCN²⁺", bullet)
                    if isinstance(bullet, str)
                    else bullet
                    for bullet in bullets
                ]
    if "光合作用" in regional_context and isinstance(slides, list):
        for slide in slides:
            bullets = slide.get("bullets") if isinstance(slide, dict) else None
            if isinstance(bullets, list):
                slide["bullets"] = [
                    bullet.replace(
                        "C₃被NADPH还原为C₅和G3P",
                        "C₃在ATP和NADPH参与下还原为G3P，部分G3P消耗ATP再生C₅",
                    )
                    if isinstance(bullet, str)
                    else bullet
                    for bullet in bullets
                ]
    if isinstance(slides, list) and len(slides) == 5:
        point_titles = [
            str(point.get("title", "")).strip()
            for point in content["knowledge_points"]
            if isinstance(point, dict) and point.get("title")
        ]
        slides.append(
            {
                "title": "课堂回顾与表达",
                "purpose": "通过复述和迁移任务检查本课学习目标",
                # 这是收尾页，交给小结版式渲染，别再退化成普通讲解页
                "layout": "summary",
                "bullets": [
                    f"回顾：{title}" for title in point_titles[:3]
                ]
                + [f"迁移任务：{content['homework_type']}"],
                "speaker_notes": (
                    "请学生用自己的话复述关键知识，并用一个新情境说明理解；"
                    "教师根据回答纠正概念偏差。"
                ),
            }
        )

    lesson_sections = data.get("lesson_sections")
    if isinstance(lesson_sections, list):
        _normalize_section_durations(
            lesson_sections,
            target_minutes=content["duration_minutes"],
        )

    for collection, id_field, prefix in (
        ("slides", "slide_id", "slide"),
        ("lesson_sections", "section_id", "section"),
        ("interactions", "interaction_id", "interaction"),
    ):
        items = data.get(collection)
        if not isinstance(items, list):
            raise TypeError(f"{collection} must be a list")
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise TypeError(f"{collection}[{index - 1}] must be an object")
            if collection == "interactions":
                _normalize_interaction_data(item, course_context=regional_context)
                _reconcile_interaction_assignments(item)
                raw_groups = item.get("answer_groups")
                if isinstance(raw_groups, dict):
                    if item.get("interaction_type") == "classification":
                        prompt = str(item.get("prompt") or "").strip()
                        missing_labels = [label for label in raw_groups if label not in prompt]
                        if missing_labels:
                            labels = "、".join(str(label) for label in raw_groups)
                            item["prompt"] = f"{prompt} 分类标签：{labels}。"
            if collection == "interactions" and not str(item.get("title") or "").strip():
                # 骨架提示词只要求给 interaction_type/prompt/items/answer_groups，
                # 而 InteractionSpec.title 必填无默认值：不补就整册校验失败。
                item["title"] = str(item.get("prompt") or "课堂互动").strip()[:40] or "课堂互动"
            if collection == "lesson_sections" and not str(item.get("title") or "").strip():
                item["title"] = f"环节 {index}"
            item[id_field] = f"{prefix}_{index:03d}"
            item["order"] = index
            item["evidence_refs"] = [
                ref.model_dump(mode="json") for ref in _bind_refs(index - 1, evidence_refs)
            ]

    interaction_ids = [item["interaction_id"] for item in data["interactions"]]
    output_specs = data.get("output_specs")
    if not isinstance(output_specs, dict):
        output_specs = {}
    # 逐个字段兜底，不能整块 setdefault：模型经常只给了 docx 的一部分（实测
    # teacher_preparation/differentiation 写成了字符串、homework 干脆没给），
    # 整块判断会让缺失字段永远空着，然后卡在质量校验上。
    point_titles = [
        str(point.get("title", "")).strip()
        for point in content["knowledge_points"]
        if isinstance(point, dict) and point.get("title")
    ]
    focus_text = content["teaching_focus"] or content["teaching_goal"] or data["title"]
    output_defaults: dict[str, dict[str, Any]] = {
        "pptx": {
            "narrative_arc": content["logic_flow"],
            "visual_direction": content["style_preference"] or "清晰、克制、便于课堂投影",
            "max_bullets_per_slide": 5,
            "speaker_notes_required": True,
        },
        "docx": {
            # 兜底文案以前是"准备课件、课堂材料和互动练习"这类放之四海皆可的空话，
            # 教师看到的就是"AI 写得很敷衍"。兜底写不出细节，至少要紧贴这节课自己的
            # 重点与知识点，而不是任何一节课都能用的句子。
            "teacher_preparation": [
                f"教具：{focus_text}的演示材料或课件",
                f"学具：围绕“{data['title']}”的练习纸与课堂记录单",
                "分组：4-6 人一组，每组指定记录员与汇报人",
            ],
            "differentiation": [
                f"需要帮扶：给出{point_titles[0] if point_titles else focus_text}的分步提示",
                "学有余力：补充一道变式任务并说明理由",
            ],
            "homework": content["homework_type"] or "用一个新情境解释本课知识并说明理由。",
            "reflection_prompts": ["学生在哪个环节暴露了理解偏差？"],
        },
        "pdf": {
            "printable_summary": (
                f"{data['title']}：掌握"
                + "、".join(point_titles or [focus_text])
                + f"；重点落在{focus_text}。"
            ),
            # 兜底也不能是空列表：整册校验要求评价清单非空，空着等于再一次失败。
            "assessment_checklist": [
                f"能够说明：{title}" for title in point_titles
            ]
            or [f"能够说明{focus_text}并解释理由"],
            "include_sources": True,
        },
        "html": {
            "completion_message": "练习完成，请结合解析回顾本课要点。",
            "allow_retry": True,
            "accessibility_notes": ["支持键盘操作", "反馈不只依赖颜色表达"],
        },
    }
    missing_output_fields: list[str] = []
    for name, defaults in output_defaults.items():
        spec = output_specs.get(name)
        if not isinstance(spec, dict):
            spec = {}
            output_specs[name] = spec
        for key, fallback in defaults.items():
            if spec.get(key) in (None, "", [], {}):
                spec[key] = fallback
                missing_output_fields.append(f"{name}.{key}")
    if missing_output_fields:
        # 兜底文案永远是"能看但敷衍"的，所以必须留下现场：没有这行日志，教师看到的
        # 只是"AI 写得很敷衍"，无从判断是模型漏写还是产品本身就这样。
        logger.warning(
            "模型未给出以下成果设定字段，已用需求单兜底：%s",
            "、".join(missing_output_fields),
        )
    html_spec = output_specs["html"]
    html_spec["interaction_ids"] = interaction_ids
    # 教具 ID 由后端派生：模型只需把教具写出来，不必再维护一份 ID 列表
    html_spec["tool_ids"] = [
        str(item.get("tool_id")) for item in (data.get("interactive_tools") or []) if item.get("tool_id")
    ]
    pptx_data = output_specs.get("pptx")
    if isinstance(pptx_data, dict) and isinstance(slides, list) and slides:
        actual_max_bullets = max(
            (
                len(slide.get("bullets", []))
                for slide in slides
                if isinstance(slide, dict) and isinstance(slide.get("bullets"), list)
            ),
            default=0,
        )
        declared_max_bullets = pptx_data.get("max_bullets_per_slide", 5)
        if (
            isinstance(declared_max_bullets, int)
            and declared_max_bullets < actual_max_bullets <= MAX_BULLETS_PER_SLIDE_LIMIT
        ):
            # 声明值描述的是"页面密度"，不是一条能把内容砍掉的裁剪线：模型写了 12 条
            # 却声明每页 5 条时，把声明值抬到 12，渲染阶段才不会被静默截断。
            pptx_data["max_bullets_per_slide"] = actual_max_bullets
    data["output_specs"] = output_specs

    data["generation_notes"] = [
        "教学蓝图由 DeepSeek 基于已确认需求生成。",
        "稳定 ID、引用证据和结构约束由后端校验并绑定。",
    ]
    # 强调标记只有幻灯片要点有渲染器承接（加粗变色），其余位置在写入时就剥掉，
    # 否则教案、打印版和互动页里会出现裸星号。
    data = limit_emphasis_to_bullets(data)
    spec = CoursewarePlanSpec.model_validate(data)

    if not MIN_SLIDES_PER_DECK <= len(spec.slides) <= MAX_SLIDES_PER_DECK:
        raise ValueError(
            f"slides must contain between {MIN_SLIDES_PER_DECK} and {MAX_SLIDES_PER_DECK} pages"
        )
    if not spec.lesson_sections:
        raise ValueError("lesson_sections must not be empty")
    section_total = sum(item.duration_minutes for item in spec.lesson_sections)
    if section_total != spec.duration_minutes:
        raise ValueError(
            f"lesson section duration total {section_total} != {spec.duration_minutes}"
        )
    if not spec.interactions:
        raise ValueError("interactions must not be empty")
    allowed_interactions = {"matching", "classification", "ordering", "quiz"}
    for interaction in spec.interactions:
        if interaction.interaction_type not in allowed_interactions:
            raise ValueError(f"unsupported interaction type: {interaction.interaction_type}")
        if not interaction.items or not interaction.answer_groups:
            raise ValueError(f"interaction {interaction.interaction_id} has no answer data")
        items = interaction.items
        if any(not item.strip() for item in items) or len(items) != len(set(items)):
            raise ValueError(
                f"interaction {interaction.interaction_id} items must be non-empty and unique"
            )
        assigned = [
            answer
            for answers in interaction.answer_groups.values()
            for answer in answers
        ]
        if not assigned or any(answer not in items for answer in assigned):
            raise ValueError(
                f"interaction {interaction.interaction_id} answers must reference items"
            )
        if interaction.interaction_type == "quiz":
            if len(interaction.answer_groups) != 1:
                raise ValueError(
                    f"quiz {interaction.interaction_id} must contain one correct-answer group"
                )
            if len(assigned) != len(set(assigned)):
                raise ValueError(
                    f"quiz {interaction.interaction_id} contains duplicate correct answers"
                )
        elif len(assigned) != len(items) or set(assigned) != set(items):
            raise ValueError(
                f"interaction {interaction.interaction_id} must assign every item exactly once"
            )
        if interaction.interaction_type in {"classification", "matching"} and any(
            not values for values in interaction.answer_groups.values()
        ):
            raise ValueError(
                f"interaction {interaction.interaction_id} contains an empty answer group"
            )
        if interaction.interaction_type == "ordering" and len(interaction.answer_groups) != 1:
            raise ValueError(
                f"ordering interaction {interaction.interaction_id} requires one ordered group"
            )
        if (
            "垃圾分类" in spec.title
            and interaction.interaction_type == "ordering"
            and "价值" in interaction.prompt
        ):
            raise ValueError("waste recycling value cannot be ordered without fixed source data")
    # 报错必须说清"哪几页"，否则日志里只能看到一句概括，修都不知道从哪修起。
    incomplete = [
        str(slide.order) for slide in spec.slides if not slide.title.strip() or not slide.purpose.strip()
    ]
    if incomplete:
        raise ValueError(f"every slide requires a title and purpose（缺标题或导语的页：{', '.join(incomplete)}）")
    pptx_spec = spec.output_specs.pptx
    # 超出"每页密度"的页面不再判失败：生成端已经不设上限，渲染器会把多出来的条目
    # 拆成续页继续投影。这里只记一行日志，出问题时能看出是密度而非内容缺陷。
    denser_than_declared = [
        f"第 {slide.order} 页 {len(slide.bullets)} 条"
        for slide in spec.slides
        if len(slide.bullets) > pptx_spec.max_bullets_per_slide
    ]
    if denser_than_declared:
        logger.info(
            "以下页面要点多于声明的每页密度，将由渲染器拆成续页：%s",
            "；".join(denser_than_declared),
        )
    if pptx_spec.speaker_notes_required:
        missing_notes = [str(slide.order) for slide in spec.slides if not slide.speaker_notes.strip()]
        if missing_notes:
            raise ValueError(
                "every slide requires speaker notes for this PPTX specification"
                f"（缺讲稿的页：{', '.join(missing_notes)}）"
            )
    if not spec.output_specs.docx.homework.strip():
        raise ValueError(
            "output_specs.docx.homework must not be empty（模型没给出作业，已尝试用需求单兜底）"
        )
    if not spec.output_specs.pdf.assessment_checklist:
        raise ValueError("output_specs.pdf.assessment_checklist must not be empty")
    return spec


def generate_courseware_spec(
    brief_content: dict[str, Any],
    evidence_refs: list[EvidenceRef],
    *,
    available_images: Iterable[dict[str, Any]] = (),
    client: OpenAI | None = None,
    on_stage: Callable[[str], None] | None = None,
    thinking: bool | None = None,
) -> CoursewareAIResult:
    """Design a blueprint, reporting every model round trip through `on_stage`.

    `on_stage` receives `generate`, `repair`, `review` or `review_repair`, which
    lets a streaming caller show which of the four round trips is running.

    `available_images` describes the pictures the teacher uploaded. It is both the
    model's menu and the allowlist: a picture reference outside it is stripped
    after every round trip, so the model can never attach a file it was not
    offered — and pictures are private uploads, so that matters.
    """

    def notify_stage(stage: str) -> None:
        if on_stage is not None:
            on_stage(stage)

    if client is None:
        if not settings.deepseek_api_key:
            raise CoursewareAIError(
                "文本 AI 服务尚未配置，无法生成 AI 教学蓝图",
                code="AI_NOT_CONFIGURED",
                recoverable=False,
            )
        try:
            client = OpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
            )
        except Exception as exc:
            # Constructing the client validates the key and base URL. Anything raised
            # here must stay inside the CoursewareAIError contract, otherwise callers
            # only see an opaque 500 instead of an actionable AI error code.
            raise CoursewareAIError(
                "AI 服务客户端初始化失败，请检查 DEEPSEEK_API_KEY 与 DEEPSEEK_BASE_URL 配置",
                code="AI_CLIENT_INIT_FAILED",
                recoverable=False,
            ) from exc

    complete = _make_completer(client, thinking)
    images = [dict(item) for item in available_images]
    allowed_image_ids = {str(item.get("material_id") or "") for item in images} - {""}
    brief = normalize_content(brief_content)
    # 学科与学段规则在这里插入：一门语文课不该读到浮力、密码强度这类条款。
    # brief 一并传入，让教师亲口提的要求（禁忌内容、风格、案例、情境、作业形式…）
    # 成为提示词里必须逐条落实的清单，而不是只躺在 user message 的 JSON 里。
    generation_prompt = build_generation_prompt(
        brief.get("subject"), brief.get("grade"), brief
    )
    review_prompt = build_review_prompt(brief.get("subject"), brief)
    context = {
        "teaching_brief": brief,
        "evidence": [item.model_dump(mode="json") for item in evidence_refs],
        "available_images": images,
        "json_schema": CoursewarePlanSpec.model_json_schema(),
    }
    messages = [
        {"role": "system", "content": generation_prompt},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
    ]

    try:
        notify_stage("generate")
        response = complete(messages)
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        _add_usage(total_usage, response)
        raw_text = response.choices[0].message.content or ""
        try:
            spec = _normalize_and_validate(
                _extract_json(raw_text),
                brief_content=brief_content,
                evidence_refs=evidence_refs,
                allowed_image_ids=allowed_image_ids,
            )
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as first_error:
            logger.warning("Repairing invalid courseware response: %s", first_error)
            truncated = _finish_reason(response) == "length"
            repair_payload = {
                # 截断时以前会让模型"写得更精炼、压到 6000 tokens 以内"——那等于用
                # 降级来掩盖输出预算不足，内容只会更单薄。现在预算已经给足，真出现
                # 截断就按"重新完整生成"来修，不主动删内容。
                "task": (
                    "上一次输出因长度限制被截断。请重新生成一份**完整且不要压缩内容**的"
                    "蓝图：宁可精简措辞，也不要删掉任何知识点、教案环节、互动题或成果设定。"
                    "只返回完整 JSON。"
                    if truncated
                    else "修复下面的蓝图，使其严格通过 schema 和校验。只返回修复后的 JSON。"
                ),
                "validation_error": str(first_error),
                "context": context,
            }
            # Re-sending a length-truncated response encourages the provider
            # to repeat and truncate it again, while also wasting input tokens.
            # Structural errors still benefit from seeing the invalid output.
            if not truncated:
                repair_payload["invalid_output"] = raw_text
            repair_messages = [
                {"role": "system", "content": generation_prompt},
                {
                    "role": "user",
                    "content": json.dumps(repair_payload, ensure_ascii=False),
                },
            ]
            notify_stage("repair")
            response = complete(repair_messages)
            _add_usage(total_usage, response)
            repaired_text = response.choices[0].message.content or ""
            try:
                spec = _normalize_and_validate(
                    _extract_json(repaired_text),
                    brief_content=brief_content,
                    evidence_refs=evidence_refs,
                    allowed_image_ids=allowed_image_ids,
                )
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as repair_error:
                logger.warning("AI repaired courseware failed validation: %s", repair_error)
                raise

        reviewed_spec = spec
        review_applied = True
        review_messages = [
            {"role": "system", "content": review_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "teaching_brief": context["teaching_brief"],
                        "evidence": context["evidence"],
                        "candidate": spec.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        notify_stage("review")
        review_response = complete(review_messages)
        _add_usage(total_usage, review_response)
        review_text = review_response.choices[0].message.content or ""
        try:
            spec = _normalize_and_validate(
                _extract_json(review_text),
                brief_content=brief_content,
                evidence_refs=evidence_refs,
                allowed_image_ids=allowed_image_ids,
            )
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as review_error:
            logger.warning("Repairing invalid reviewed courseware response: %s", review_error)
            if _finish_reason(review_response) == "length":
                logger.warning(
                    "Reviewed courseware hit the output limit; keeping validated candidate"
                )
                spec = reviewed_spec
                review_applied = False
            else:
                review_repair_messages = [
                    {"role": "system", "content": review_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": "只修复审校结果的 JSON 结构和约束，保留事实修正。返回完整 JSON。",
                                "validation_error": str(review_error),
                                "invalid_reviewed_output": review_text,
                                "teaching_brief": context["teaching_brief"],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
                notify_stage("review_repair")
                review_response = complete(review_repair_messages)
                _add_usage(total_usage, review_response)
                try:
                    spec = _normalize_and_validate(
                        _extract_json(review_response.choices[0].message.content or ""),
                        brief_content=brief_content,
                        evidence_refs=evidence_refs,
                        allowed_image_ids=allowed_image_ids,
                    )
                except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as repair_error:
                    logger.warning(
                        "AI repaired review failed validation; keeping validated candidate: %s",
                        repair_error,
                    )
                    spec = reviewed_spec
                    review_applied = False
        if review_applied:
            spec.generation_notes.append("教学事实和互动可判定性已通过独立 AI 审校。")
        else:
            spec.generation_notes.append("AI 审校响应结构无效，已保留通过校验的生成蓝图。")
        spec = _carry_interactive_tools(spec, reviewed_spec)
        return CoursewareAIResult(
            spec=spec,
            model_name=MODEL_NAME,
            prompt_version=PROMPT_VERSION,
            usage=total_usage,
        )
    except RateLimitError as exc:
        raise CoursewareAIError("AI 服务请求过于频繁", code="AI_RATE_LIMITED") from exc
    except APITimeoutError as exc:
        raise CoursewareAIError("AI 教学蓝图生成超时", code="AI_TIMEOUT") from exc
    except APIConnectionError as exc:
        raise CoursewareAIError("无法连接 AI 服务", code="AI_CONNECTION_FAILED") from exc
    except APIStatusError as exc:
        raise CoursewareAIError("AI 服务暂时不可用", code="AI_PROVIDER_ERROR") from exc
    except CoursewareAIError:
        raise
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise CoursewareAIError(
            "AI 返回的教学蓝图或审校结果未通过结构校验",
            code="AI_INVALID_RESPONSE",
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected courseware AI error")
        raise CoursewareAIError("AI 教学蓝图生成失败", code="AI_REQUEST_FAILED") from exc


# ── 流水线模式（Agentic）────────────────────────────────
#
# 经典模式一次调用要塞下 slides + 教案 + 互动 + output_specs，而输出有长度上限，
# 模型只能把每条都写短 —— 教案四项、PDF 学习要点尤其明显。这里拆成四段，每段只做
# 一件事，每段都有完整的输出预算：
#   1) skeleton      搭骨架：页数、layout、标题、导语、要点草案、环节与互动草案
#   2) fill_teaching 填教案四项与四类成果设定（含 PDF 学习要点与达成判准）
#   3) fill_slides   分批写每页要点正文与讲稿
#   4) review        与经典模式同一套审校规则（按学科注入易错点）
# 每段都先让模型用自然语言写下判断（analysis / plan_notes / draft_notes / findings），
# 这些思考只进日志，不进产物，因此不影响 JSON 结构与下游渲染。

AGENTIC_PROMPT_VERSION = "courseware-plan-v24-agentic-scene"
# 每批填充几页。质量优先：一页一次调用，把这一页的输出预算全部留给它自己。
# 整册一次写会让每条都被写短（这是"内容单薄"的根因），分批到页级则是同一逻辑的
# 彻底版本——页数多时调用次数会线性增加，这是刻意的取舍。
FILL_SLIDES_BATCH = 1
# 页级调用是串行的下游：12 页 12 次往返、每次几十秒，合计十分钟级，教师会以为
# 页面卡死。批次之间彼此独立（各写各的页），所以并发发出，墙钟时间按并发数缩短。
FILL_SLIDES_CONCURRENCY = 4
# 单步 JSON 解析失败时的尝试次数（首次 + 重发一次）。模型偶尔会把 JSON 输出到截断
# （见 complete_json 的说明），原样重发往往就能拿到完整结果；再不行才降级交给兜底。
_JSON_ATTEMPTS = 2


def _log_thinking(stage: str, value: Any) -> None:
    """把模型该轮的自然语言思考写进日志（不写进产物）。"""
    if value in (None, "", [], {}):
        return
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    logger.info("[agentic:%s] 模型思考：%s", stage, text[:4000])


def _batched(items: list, size: int) -> list[list]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _order_key(value: Any) -> Any:
    """页码的可比较键。

    模型指代页码的方式很不统一，实测同一份蓝图的三个批次分别给出 ``'slide_1'``、
    ``'slide_5'``、``9``。只认 int 会让整批填充内容落空（要点退回骨架草案、讲稿为空），
    所以这里把数字形式的字符串也归一到 int。
    """
    if isinstance(value, bool) or isinstance(value, int):
        return value
    text = str(value if value is not None else "").strip()
    if not text:
        return value
    if text.isdigit():
        return int(text)
    if text.startswith("-") and text[1:].isdigit():
        return int(text)
    digits = re.findall(r"\d+", text)
    if len(digits) == 1:
        return int(digits[0])
    return value


def _validation_summary(error: Exception, *, with_input: bool = True) -> str:
    """把校验错误压成一行摘要：日志里真正要看的是"哪个字段错了、错成了什么样"。

    带上出错值的短预览，是因为"类型不对"这类问题只有看到实际值才知道该怎么归一化
    （教师看到的提示永远只是"生成失败"，日志是唯一的现场）。
    """
    if isinstance(error, ValidationError):
        parts = []
        for item in error.errors()[:8]:
            location = ".".join(str(piece) for piece in item.get("loc", ()))
            message = f"{location}: {item.get('msg', '')}"
            if with_input and "input" in item:
                preview = json.dumps(item["input"], ensure_ascii=False, default=str)
                message += f"（实际值：{preview[:120]}）"
            parts.append(message)
        return "；".join(parts) or str(error)
    text = str(error).strip()
    return text.splitlines()[0] if text else type(error).__name__


def _as_text(value: Any) -> str:
    """把任意模型输出压成一段可直接展示的文字。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "；".join(text for text in (_as_text(item) for item in value) if text)
    if isinstance(value, dict):
        return "；".join(
            f"{key}：{_as_text(item)}" for key, item in value.items() if _as_text(item)
        )
    return "" if value is None else str(value)


def _as_text_list(value: Any) -> list[str]:
    """把字符串/字典归一成字符串列表。

    模型经常把"2-4 条"写成一段话（实测 teacher_actions 就是整段带时间节点的叙述），
    而 schema 要 list[str]。这里只做切分，不改写内容。
    """
    if isinstance(value, list):
        return [text for text in (_as_text(item) for item in value) if text]
    if isinstance(value, dict):
        return [text for text in (_as_text(item) for item in value.values()) if text]
    if not isinstance(value, str):
        return []
    text = value.strip()
    if not text:
        return []
    for separator in ("\n", "。", "；", ";"):
        parts = [part.strip(" \t　。；;") for part in text.split(separator)]
        parts = [part for part in parts if part]
        if len(parts) > 1:
            return parts
    return [text]


# 渲染器只支持这四种互动；模型偶尔会自造题型（实测出现过"找错图解""当堂互评"）。
_SUPPORTED_INTERACTION_TYPES = ("matching", "classification", "ordering", "quiz")


def _normalize_interaction_shape(item: dict[str, Any]) -> None:
    """把互动题的形状拉回 schema：题型、题干、条目、答案分组。"""
    prompt = _as_text(item.get("prompt"))
    if not prompt:
        prompt = _as_text(item.get("title")) or "请完成下面的练习。"
    item["prompt"] = prompt
    if not _as_text(item.get("title")):
        item["title"] = prompt[:40]

    items = [text for text in _as_text_list(item.get("items")) if text]
    item["items"] = items

    raw_groups = item.get("answer_groups")
    groups: dict[str, list[str]] = {}
    if isinstance(raw_groups, dict):
        for label, values in raw_groups.items():
            texts = _as_text_list(values)
            if texts:
                groups[str(label)] = texts
    elif isinstance(raw_groups, list):
        texts = _as_text_list(raw_groups)
        if texts and len(texts) == len(items):
            # 模型常见的写法：answer_groups 与 items 平行的两个数组。
            # 每条答案都是它自己写的，这里只是把平行数组配成"配对题"，
            # 不替它编答案。
            groups = {text[:40] or f"答案 {index}": [entry] for index, (text, entry) in enumerate(zip(texts, items), start=1)}
    item["answer_groups"] = groups

    if str(item.get("interaction_type") or "") not in _SUPPORTED_INTERACTION_TYPES:
        original = item.get("interaction_type")
        if len(groups) == 1:
            item["interaction_type"] = "quiz"
        elif len(groups) > 1:
            item["interaction_type"] = "matching"
        else:
            item["interaction_type"] = "classification"
        logger.warning(
            "互动题型 %r 不在支持范围内，已按答案形状归一为 %s",
            original,
            item["interaction_type"],
        )


_OUTPUT_LIST_FIELDS: dict[str, tuple[str, ...]] = {
    "pptx": ("narrative_arc",),
    "docx": ("teacher_preparation", "differentiation", "reflection_prompts"),
    "pdf": ("assessment_checklist",),
    "html": ("interaction_ids", "accessibility_notes"),
}
_OUTPUT_TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "pptx": ("visual_direction",),
    "docx": ("homework",),
    "pdf": ("printable_summary",),
    "html": ("completion_message",),
}
# 模型自造的键名（实测 html 用了 interactive_id / completion_feedback / accessibility）
_OUTPUT_KEY_ALIASES = {
    "interaction_id": "interaction_ids",
    "interactive_id": "interaction_ids",
    "completion_feedback": "completion_message",
    "accessibility": "accessibility_notes",
}


def _coerce_output_specs(specs: dict[str, Any]) -> None:
    for spec in specs.values():
        if not isinstance(spec, dict):
            continue
        for old, new in _OUTPUT_KEY_ALIASES.items():
            if old in spec and new not in spec:
                spec[new] = spec.pop(old)
        for key in ("max_bullets_per_slide",):
            if key in spec:
                try:
                    spec[key] = int(spec[key])
                except (TypeError, ValueError):
                    spec.pop(key, None)
        for key in ("speaker_notes_required", "allow_retry", "include_sources"):
            if key in spec and isinstance(spec[key], str):
                spec[key] = spec[key].strip().lower() in {"true", "是", "yes", "1"}
    for name, fields in _OUTPUT_LIST_FIELDS.items():
        spec = specs.get(name)
        if isinstance(spec, dict):
            for field in fields:
                if field in spec and not isinstance(spec[field], list):
                    spec[field] = _as_text_list(spec[field])
    for name, fields in _OUTPUT_TEXT_FIELDS.items():
        spec = specs.get(name)
        if isinstance(spec, dict):
            for field in fields:
                if field in spec and not isinstance(spec[field], str):
                    spec[field] = _as_text(spec[field])


_TOOL_ENGINE_ALIASES: dict[str, str] = {
    "pipe": "flow",
    "fluid": "flow",
    "flowing": "flow",
    "bernoulli": "flow",
    "line": "curve",
    "graph": "curve",
    "chart": "curve",
    "plot": "curve",
    "function": "curve",
    "vector": "field",
    "gradient": "field",
    "particle": "particles",
    "diffusion": "particles",
    "force": "balance",
    "lever": "balance",
    "electric": "circuit",
    "circuitry": "circuit",
    # "随时间演化的过程"一类场景：模型常自造这些引擎名，统一落到 scene
    "orbit": "scene",
    "simulation": "scene",
    "sim": "scene",
    "animation": "scene",
    "dynamic": "scene",
}

# 一册蓝图保留的教具上限（提示词要求 1-2 个，修复合并时同样封顶）
_TOOL_LIMIT = 2


def _tool_issue(
    report: list[dict[str, Any]] | None,
    *,
    index: int,
    tool_id: str,
    title: str,
    stage: str,
    reason: str,
) -> None:
    """记录一处教具被拒/降级的理由：始终落日志；有报告时留给修复轮。

    ``report`` 为 None 时（经典管线、审校归一化等）行为与从前逐位一致，只是日志
    照旧；agentic 管线会把报告交给"教具修复"那一轮模型请求，被拒的教具不再静默消失。
    """
    logger.warning("[agentic] %s", reason)
    if report is None:
        return
    report.append(
        {
            "index": index,
            "tool_id": tool_id,
            "title": title,
            "stage": stage,
            "reason": reason,
        }
    )


def _as_number(value: Any) -> float | None:
    """模型经常把数字写成字符串（"0.5"、"1,000"），统一转成 float。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "")
    match = re.search(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _normalize_tool_variables(
    raw: Any,
    *,
    report: list[dict[str, Any]] | None = None,
    index: int = 0,
    tool_id: str = "",
    title: str = "",
) -> list[dict[str, Any]]:
    """参数：key/label/单位/区间/默认值。区间写反就换回来，默认值越界就夹回区间。"""
    if not isinstance(raw, list):
        return []
    variables: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in seen:
            _tool_issue(
                report,
                index=index,
                tool_id=tool_id,
                title=title,
                stage="variable",
                reason=f"教具参数名不可用，已跳过：{item.get('key')!r}",
            )
            continue
        low = _as_number(item.get("min"))
        high = _as_number(item.get("max"))
        default = _as_number(item.get("default"))
        if low is None or high is None:
            _tool_issue(
                report,
                index=index,
                tool_id=tool_id,
                title=title,
                stage="variable",
                reason=f"教具参数 {key} 缺少区间，已跳过",
            )
            continue
        if low > high:
            low, high = high, low
        if low == high:
            high = low + 1.0
        if default is None or not low <= default <= high:
            default = (low + high) / 2
        step = _as_number(item.get("step"))
        if step is None or step <= 0:
            step = max((high - low) / 100.0, 1e-6)
        seen.add(key)
        variables.append(
            {
                "key": key,
                "label": _as_text(item.get("label")) or key,
                "unit": _as_text(item.get("unit")),
                "min": low,
                "max": high,
                "default": default,
                "step": step,
                "role": _as_text(item.get("role")),
            }
        )
    return variables


def _normalize_tool_constants(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    constants: dict[str, float] = {}
    for name, value in raw.items():
        key = str(name or "").strip()
        number = _as_number(value)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) and number is not None:
            constants[key] = number
    return constants


def _normalize_tool_outputs(
    raw: Any,
    variables: list[dict[str, Any]],
    constants: dict[str, float],
    *,
    report: list[dict[str, Any]] | None = None,
    index: int = 0,
    tool_id: str = "",
    title: str = "",
) -> list[dict[str, Any]]:
    """读数：公式必须能用白名单求值算出来，否则这一条丢掉。

    这是整套教具的安全阀：模型写的公式引用不存在的量、或用白名单外的语法，
    都在这里被拒，产出的 HTML 只包含校验通过的公式。
    """
    if not isinstance(raw, list):
        return []
    known = {item["key"] for item in variables} | set(constants)
    defaults = {item["key"]: float(item["default"]) for item in variables}
    defaults.update(constants)
    outputs: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        expression = str(item.get("expression") or "").strip()
        if not key or not expression:
            _tool_issue(
                report,
                index=index,
                tool_id=tool_id,
                title=title,
                stage="output",
                reason="教具读数缺少 key 或 expression，已跳过",
            )
            continue
        try:
            run, _ = compile_expression(expression, known)
            value = run(defaults)
        except ExpressionError as exc:
            _tool_issue(
                report,
                index=index,
                tool_id=tool_id,
                title=title,
                stage="output",
                reason=f"教具读数 {key} 的公式被拒：{exc}（{expression[:60]}）",
            )
            continue
        if not math.isfinite(value):
            _tool_issue(
                report,
                index=index,
                tool_id=tool_id,
                title=title,
                stage="output",
                reason=f"教具读数 {key} 在默认参数下算不出有限值，已丢弃",
            )
            continue
        outputs.append(
            {
                "key": key,
                "label": _as_text(item.get("label")) or key,
                "unit": _as_text(item.get("unit")),
                "expression": expression,
                "hint": _as_text(item.get("hint")),
            }
        )
    return outputs


def _tool_responds_to_variables(
    variables: list[dict[str, Any]],
    constants: dict[str, float],
    outputs: list[dict[str, Any]],
) -> bool:
    """至少有一个读数会随某个参数变化 —— 否则这个滑块是玩具，不是教具。"""
    known = {item["key"] for item in variables} | set(constants)
    base = {item["key"]: float(item["default"]) for item in variables}
    base.update(constants)
    compiled = []
    for item in outputs:
        try:
            run, _ = compile_expression(item["expression"], known)
        except ExpressionError:
            continue
        compiled.append(run)
    if not compiled:
        return False
    try:
        baseline = [run(base) for run in compiled]
    except ExpressionError:
        return False
    for variable in variables:
        span = (variable["max"] - variable["min"]) or 1.0
        probe = dict(base)
        probe[variable["key"]] = float(variable["default"]) + span / 3.0
        if not variable["min"] <= probe[variable["key"]] <= variable["max"]:
            probe[variable["key"]] = float(variable["min"])
        for index, run in enumerate(compiled):
            try:
                if abs(run(probe) - baseline[index]) > 1e-9:
                    return True
            except ExpressionError:
                continue
    return False


def _scene_token(value: Any, default: str) -> str:
    """颜色只认调色板 token（外加 none）；不认识的 token 静默回到默认色。"""
    token = str(value or "").strip().lower()
    if token == "none" or token in SCENE_PALETTE:
        return token
    return default


def _scene_clamp(value: Any, low: float, high: float, default: float) -> float:
    number = _as_number(value)
    if number is None:
        return default
    return min(max(number, low), high)


def _scene_dash(value: Any) -> list[float]:
    raw = value if isinstance(value, list) else [value]
    dashes: list[float] = []
    for item in raw[:2]:
        number = _as_number(item)
        if number is not None and number > 0:
            dashes.append(min(max(number, 1.0), 40.0))
    return dashes


def _normalize_tool_scene(
    raw: Any,
    *,
    variables: list[dict[str, Any]],
    constants: dict[str, float],
    outputs: list[dict[str, Any]],
    index: int,
    tool_id: str,
    title: str,
    report: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """动态场景：逐实体校验表达式几何属性；返回 None 表示降级为 curve 渲染。

    与读数公式同一道安全闸：几何属性只能是受限表达式，作用域是参数 ∪ 常量 ∪
    读数 ∪ 场景时钟 t（path 的采样参数另加 s），逐条走 compile_expression，并在
    若干时刻做有限性探针。坏实体单独丢弃、理由写进报告——万有引力事故里公式
    引用了未声明的 G、M，以前整个教具被静默丢掉，现在会带着原因进入修复轮。
    """

    def fail(reason: str) -> None:
        _tool_issue(
            report, index=index, tool_id=tool_id, title=title, stage="scene", reason=reason
        )

    if not isinstance(raw, dict) or not isinstance(raw.get("entities"), list) or not raw["entities"]:
        fail("scene 结构无效（没有 entities），已降级为 curve 渲染")
        return None
    variable_names = {item["key"] for item in variables}
    output_names = {item["key"] for item in outputs}
    clash = sorted((variable_names | set(constants) | output_names) & set(SCENE_RESERVED_NAMES))
    if clash:
        fail(
            f"scene 用 t/s 作场景时钟与采样参数，但教具自己占用了 {'、'.join(clash)}，"
            "已降级为 curve 渲染"
        )
        return None
    known = variable_names | set(constants) | output_names
    scope_base = {item["key"]: float(item["default"]) for item in variables}
    scope_base.update(constants)
    for item in outputs:
        try:
            run, _ = compile_expression(item["expression"], variable_names | set(constants))
            scope_base[item["key"]] = float(run(scope_base))
        except ExpressionError:
            continue
    time_probes = (0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0)
    entities: list[dict[str, Any]] = []
    tracks_controls = False
    for position, item in enumerate(raw["entities"]):
        if len(entities) >= SCENE_MAX_ENTITIES:
            fail(f"scene 实体数量超过上限 {SCENE_MAX_ENTITIES}，其余已丢弃")
            break
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        label = f"第 {position + 1} 个实体"
        if kind not in SCENE_ENTITY_KINDS:
            fail(f"场景{label}的 kind {kind!r} 不在图元白名单，已丢弃该实体")
            continue
        missing = [prop for prop in SCENE_KIND_REQUIRED_PROPS[kind] if item.get(prop) in (None, "")]
        if missing:
            fail(f"场景{label}（{kind}）缺少属性 {'、'.join(missing)}，已丢弃该实体")
            continue
        probe_names = known | {"t"} | ({"s"} if kind == "path" else set())
        points: list[tuple[float, float | None]] = [(probe, None) for probe in time_probes]
        if kind == "path":
            points = [(probe, sample) for probe in time_probes for sample in (0.0, 0.5, 1.0)]
        entity: dict[str, Any] = {"kind": kind}
        entity_tracks = False
        failure = ""
        for prop in SCENE_KIND_EXPRESSION_PROPS[kind]:
            expression = str(item.get(prop) or "").strip()
            try:
                run, _ = compile_expression(expression, probe_names)
            except ExpressionError as exc:
                failure = f"属性 {prop} 的公式被拒：{exc}"
                break
            if referenced_names(expression) & (variable_names | output_names):
                entity_tracks = True
            for probe_t, probe_s in points:
                scope = dict(scope_base)
                scope["t"] = probe_t
                at = f"t={probe_t:g}"
                if probe_s is not None:
                    scope["s"] = probe_s
                    at += f"、s={probe_s:g}"
                try:
                    value = run(scope)
                except ExpressionError as exc:
                    failure = f"属性 {prop} 在 {at} 处算不出值：{exc}"
                    break
                if not math.isfinite(value):
                    failure = f"属性 {prop} 在 {at} 处算出非有限值"
                    break
            if failure:
                break
            entity[prop] = expression
        if failure:
            fail(f"场景{label}（{kind}）{failure}，已丢弃该实体")
            continue
        if kind == "readout":
            output_key = str(item.get("output") or "").strip()
            if output_key not in output_names:
                fail(f"场景{label}（readout）指向不存在的读数 {output_key!r}，已丢弃该实体")
                continue
            entity["output"] = output_key
            entity["size"] = _scene_clamp(item.get("size"), 8, 28, 14)
            entity["align"] = (
                item.get("align") if item.get("align") in ("left", "center", "right") else "left"
            )
            entity["fill"] = _scene_token(item.get("fill"), "slate")
        elif kind == "text":
            content = _as_text(item.get("content"))[:SCENE_TEXT_MAX_LENGTH]
            if not content:
                fail(f"场景{label}（text）的 content 为空，已丢弃该实体")
                continue
            entity["content"] = content
            entity["size"] = _scene_clamp(item.get("size"), 8, 28, 16)
            entity["align"] = (
                item.get("align") if item.get("align") in ("left", "center", "right") else "left"
            )
            entity["fill"] = _scene_token(item.get("fill"), "ink")
        else:
            entity["stroke"] = _scene_token(item.get("stroke"), "primary")
            entity["stroke_width"] = _scene_clamp(item.get("stroke_width"), 0.5, 12, 2)
            dashes = _scene_dash(item.get("dash"))
            if dashes:
                entity["dash"] = dashes
            if kind in ("circle", "rect", "path"):
                entity["fill"] = _scene_token(item.get("fill"), "none")
            if kind == "rect":
                entity["radius"] = _scene_clamp(item.get("radius"), 0, 40, 0)
            if kind == "arrow":
                entity["head"] = _scene_clamp(item.get("head"), 4, 24, 12)
            if kind == "path":
                entity["samples"] = int(
                    _scene_clamp(item.get("samples"), 2, SCENE_MAX_PATH_SAMPLES, 120)
                )
        tracks_controls = tracks_controls or entity_tracks
        entities.append(entity)
    if not entities:
        fail("scene 里没有可绘制的实体，已降级为 curve 渲染")
        return None
    if not tracks_controls:
        fail("scene 里没有任何元素随参数或读数变化（纯装饰动画），已降级为 curve 渲染")
        return None
    background = str(raw.get("background") or "").strip().lower()
    scene: dict[str, Any] = {
        "background": background if background in SCENE_PALETTE else "white",
        "entities": entities,
    }
    loop = _as_number(raw.get("loop"))
    if loop is not None and loop > 0:
        scene["loop"] = min(loop, 3600.0)
    return scene


def _normalize_tools(raw: Any, *, report: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """把模型给的互动教具拉回 schema；坏教具丢弃并记日志（不让整册校验失败）。

    教具是"锦上添花"的一层：宁可这一课没有教具（回落到互动题），也不能因为它写坏
    而让整份蓝图生成失败。``report`` 非空时逐条记下被拒/降级的理由（含 scene 实体），
    供 agentic 管线发起一轮"教具修复"——拒绝不再静默。
    """
    if not isinstance(raw, list):
        return []
    tools: list[dict[str, Any]] = []
    for position, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        tool_id = str(item.get("tool_id") or "").strip()
        title = _as_text(item.get("title"))
        engine = str(item.get("engine") or "").strip().lower()
        engine = _TOOL_ENGINE_ALIASES.get(engine, engine)
        if engine not in TOOL_ENGINES:
            _tool_issue(
                report,
                index=position,
                tool_id=tool_id,
                title=title,
                stage="engine",
                reason=f"教具引擎 {engine!r} 不在白名单，按 curve 渲染",
            )
            engine = "curve"
        variables = _normalize_tool_variables(
            item.get("variables"), report=report, index=position, tool_id=tool_id, title=title
        )
        constants = _normalize_tool_constants(item.get("constants"))
        outputs = _normalize_tool_outputs(
            item.get("outputs"),
            variables,
            constants,
            report=report,
            index=position,
            tool_id=tool_id,
            title=title,
        )
        if not variables or not outputs:
            _tool_issue(
                report,
                index=position,
                tool_id=tool_id,
                title=title,
                stage="tool",
                reason=f"丢弃一个教具（缺少可用参数或读数）：{title[:40]}",
            )
            continue
        if not _tool_responds_to_variables(variables, constants, outputs):
            _tool_issue(
                report,
                index=position,
                tool_id=tool_id,
                title=title,
                stage="tool",
                reason=f"丢弃一个教具（没有任何读数随参数变化，等于玩具）：{title[:40]}",
            )
            continue
        scene: dict[str, Any] = {}
        if engine == "scene":
            normalized_scene = _normalize_tool_scene(
                item.get("scene"),
                variables=variables,
                constants=constants,
                outputs=outputs,
                index=position,
                tool_id=tool_id,
                title=title,
                report=report,
            )
            if normalized_scene is None:
                # scene 不可用：保留参数与读数，按 curve 渲染（比整件丢弃更有用）
                engine = "curve"
            else:
                scene = normalized_scene
        steps = [text for text in (_as_text(step) for step in (item.get("guided_steps") or [])) if text]
        tools.append(
            {
                "tool_id": tool_id or f"tool_{len(tools) + 1:03d}",
                "order": len(tools) + 1,
                "title": title or "课堂探究工具",
                "goal": _as_text(item.get("goal")),
                "engine": engine,
                "model_note": _as_text(item.get("model_note")),
                "variables": variables,
                "outputs": outputs,
                "constants": constants,
                "scene": scene,
                "predict_prompts": [
                    text
                    for text in (_as_text(prompt) for prompt in (item.get("predict_prompts") or []))
                    if text
                ],
                "guided_steps": steps,
                "check_questions": [
                    text
                    for text in (
                        _as_text(question) for question in (item.get("check_questions") or [])
                    )
                    if text
                ],
            }
        )
    return tools


def _tool_repair_messages(
    raw_tools: list[Any],
    issues: list[dict[str, Any]],
    system_prompt: str,
) -> list[dict[str, str]]:
    """一次专门的"教具修复"请求：原始教具 + 逐条中文拒绝原因。"""
    offending: list[dict[str, Any]] = []
    seen: set[int] = set()
    for issue in issues:
        position = int(_as_number(issue.get("index")) or 0)
        if position in seen or not 0 < position <= len(raw_tools):
            continue
        seen.add(position)
        offending.append(
            {
                "index": position,
                "tool_id": issue.get("tool_id") or "",
                "title": issue.get("title") or "",
                "problem": issue.get("reason") or "",
                "tool": raw_tools[position - 1],
            }
        )
    request = {
        "task": (
            "下面这些互动教具（或其中的元素）没有通过结构校验，请逐条按 problem 修复，"
            "保持原来的教学意图；修复后的教具保留原 tool_id，并把它在原列表里的序号放进 "
            "index 字段。公式里用到的每个物理常量都必须先写进 constants，不允许当作已知量"
            "直接引用。只返回修复后的教具，格式为 "
            '{"interactive_tools": [...]}，不要返回整册蓝图。'
        ),
        "offending": offending,
    }
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
    ]


def _repaired_tools(patch: Any) -> list[Any]:
    """从修复响应里取教具列表；形状不对就当没有修出可用结果。"""
    raw = patch.get("interactive_tools") if isinstance(patch, dict) else patch
    return raw if isinstance(raw, list) else []


def _merge_repaired_tools(
    raw_tools: list[Any],
    issues: list[dict[str, Any]],
    repaired: list[Any],
    *,
    limit: int = _TOOL_LIMIT,
) -> list[Any]:
    """把修复响应并回原始教具列表：被点名的按 tool_id / index 替换，其余原样保留。"""
    if not repaired:
        return raw_tools[:limit]
    replacements: dict[int, Any] = {}
    leftovers = [item for item in repaired if isinstance(item, dict)]
    for issue in issues:
        position = int(_as_number(issue.get("index")) or 0)
        if not 0 < position <= len(raw_tools):
            continue
        original = raw_tools[position - 1]
        original_id = ""
        if isinstance(original, dict):
            original_id = str(original.get("tool_id") or "").strip()
        for candidate in leftovers:
            candidate_id = str(candidate.get("tool_id") or "").strip()
            candidate_position = _as_number(candidate.get("index"))
            if (original_id and candidate_id == original_id) or (
                candidate_position is not None and int(candidate_position) == position
            ):
                replacements[position] = candidate
                leftovers.remove(candidate)
                break
    merged = [replacements.get(position, item) for position, item in enumerate(raw_tools, start=1)]
    # 模型可能顺手补出新教具：追加在末尾，整体仍受 limit 封顶
    merged.extend(leftovers)
    return merged[:limit]


def _coerce_model_shapes(data: dict[str, Any]) -> None:
    """把模型给出的字段形状拉回 schema 期望的形状。

    "宽松入参、严格出参"：模型不是每次都能照 schema 写字（实测同一份蓝图里同时出现
    字符串当列表用、平行数组当字典用、自造题型与自造键名），整册校验却必须严格。
    这里只纠正形状，不替模型编内容。
    """
    data["interactive_tools"] = _normalize_tools(data.get("interactive_tools"))
    for slide in data.get("slides") or []:
        if isinstance(slide, dict):
            slide["bullets"] = _as_text_list(slide.get("bullets"))
            for key in ("title", "purpose", "layout", "speaker_notes"):
                if key in slide:
                    slide[key] = _as_text(slide[key])
    for section in data.get("lesson_sections") or []:
        if not isinstance(section, dict):
            continue
        for key in ("teacher_actions", "student_actions"):
            section[key] = _as_text_list(section.get(key))
        for key in ("title", "objective", "assessment"):
            if key in section:
                section[key] = _as_text(section[key])
        try:
            section["duration_minutes"] = max(1, int(section.get("duration_minutes")))
        except (TypeError, ValueError):
            # 交给 _normalize_section_durations 按权重补齐
            section.pop("duration_minutes", None)
    for item in data.get("interactions") or []:
        if isinstance(item, dict):
            _normalize_interaction_shape(item)
    specs = data.get("output_specs")
    if isinstance(specs, dict):
        _coerce_output_specs(specs)


def _missing_speaker_notes(data: dict[str, Any]) -> list[Any]:
    """缺讲稿的页码。"""
    return [
        slide.get("order")
        for slide in (data.get("slides") or [])
        if isinstance(slide, dict) and not str(slide.get("speaker_notes") or "").strip()
    ]


def _offending_slices(data: dict[str, Any], error: Exception) -> dict[str, Any]:
    """只挑出"出错的那几块"用于修复请求。

    让模型重发整册，在页数多时几乎必然被输出上限截断（实测 13 页的整册 JSON 就断在
    半路，修复反而失败）。只发出错的部分，响应自然也小。
    """
    if not isinstance(error, ValidationError):
        message = str(error)
        if "speaker notes" in message:
            missing = {_order_key(order) for order in _missing_speaker_notes(data)}
            return {
                "slides": [
                    slide
                    for slide in (data.get("slides") or [])
                    if isinstance(slide, dict) and _order_key(slide.get("order")) in missing
                ]
            }
        if "title and purpose" in message:
            return {
                "slides": [
                    slide
                    for slide in (data.get("slides") or [])
                    if isinstance(slide, dict)
                    and (not str(slide.get("title") or "").strip() or not str(slide.get("purpose") or "").strip())
                ]
            }
        if "output_specs" in message or "homework" in message or "assessment_checklist" in message:
            return {"output_specs": data.get("output_specs") or {}}
        if "interaction" in message:
            return {"interactions": data.get("interactions") or []}
        if "lesson section" in message or "duration" in message:
            return {"lesson_sections": data.get("lesson_sections") or []}
        return dict(data)

    slices: dict[str, Any] = {}
    for item in error.errors()[:12]:
        location = list(item.get("loc") or ())
        if not location:
            continue
        head = location[0]
        if head not in data:
            continue
        if len(location) >= 2 and isinstance(location[1], int):
            items = data[head]
            if isinstance(items, list) and 0 <= location[1] < len(items):
                bucket = slices.setdefault(head, [])
                if items[location[1]] not in bucket:
                    bucket.append(items[location[1]])
                continue
        slices[head] = data[head]
    return slices or dict(data)


def _merge_blueprint_patch(data: dict[str, Any], patch: Any) -> bool:
    """把"只改出错部分"的补丁并回蓝图，返回是否真的改动过。"""
    if not isinstance(patch, dict):
        return False
    body = patch.get("blueprint") if isinstance(patch.get("blueprint"), dict) else patch
    changed = False

    for collection in ("slides", "lesson_sections"):
        items = body.get(collection)
        if not isinstance(items, list):
            continue
        targets = [item for item in (data.get(collection) or []) if isinstance(item, dict)]
        index = {_order_key(item.get("order")): item for item in targets}
        for position, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            target = index.get(_order_key(item.get("order")))
            if target is None and position < len(targets):
                target = targets[position]
            if target is None:
                continue
            for field, value in item.items():
                if field in {"order", "slide_id", "section_id"}:
                    continue
                target[field] = value
                changed = True

    interactions = body.get("interactions")
    if isinstance(interactions, list) and interactions:
        data["interactions"] = interactions
        changed = True

    specs = body.get("output_specs")
    if isinstance(specs, dict):
        current = data.get("output_specs")
        if not isinstance(current, dict):
            current = {}
            data["output_specs"] = current
        for name, spec in specs.items():
            if isinstance(spec, dict):
                base = current.get(name)
                if not isinstance(base, dict):
                    base = {}
                    current[name] = base
                base.update(spec)
                changed = True

    if str(body.get("title") or "").strip():
        data["title"] = body["title"]
        changed = True
    return changed


def _merge_section_baselines(baseline: Any, filled: Any) -> list[Any]:
    """把骨架里每个环节的必填字段并回填充结果。

    FILL_TEACHING_RULES 只要求模型"写全四项"，没有要求原样带回 title 与
    duration_minutes，而这两者在 LessonPlanSectionSpec 里是必填 —— 直接采用模型
    返回的数组，就会在整册校验时被拒（title Field required），教师只看到"生成失败"。
    """
    base_items = baseline if isinstance(baseline, list) else []
    if not isinstance(filled, list) or not filled:
        return base_items
    base_by_order = {
        item.get("order"): item for item in base_items if isinstance(item, dict)
    }
    merged: list[Any] = []
    for index, item in enumerate(filled, start=1):
        if not isinstance(item, dict):
            merged.append(item)
            continue
        base = base_by_order.get(item.get("order")) or {}
        if not str(item.get("title") or "").strip():
            item["title"] = str(base.get("title") or f"环节 {index}").strip()
        if not item.get("duration_minutes"):
            item["duration_minutes"] = base.get("duration_minutes") or 1
        if not str(item.get("objective") or "").strip():
            item["objective"] = str(base.get("objective") or "").strip()
        merged.append(item)
    return merged


def _carry_interactive_tools(
    spec: CoursewarePlanSpec,
    candidate: CoursewarePlanSpec,
) -> CoursewarePlanSpec:
    """审校不该把互动教具弄丢。

    审校段的职责是核对事实与可判定性，模型通常只回"修正后的内容"，教具整段省略。
    省略不等于删除：这里把生成阶段的教具接回来，并重新派生 HTML 的 tool_ids
    （教具 ID 是后端派生的，审校输出里不会有）。
    """
    if not spec.interactive_tools and candidate.interactive_tools:
        logger.info("审校结果未包含互动教具，沿用生成阶段的 %s 个", len(candidate.interactive_tools))
        spec = spec.model_copy(update={"interactive_tools": candidate.interactive_tools})
    if not spec.output_specs.html.tool_ids and spec.interactive_tools:
        spec.output_specs.html.tool_ids = [tool.tool_id for tool in spec.interactive_tools]
    return spec


def generate_courseware_spec_agentic(
    brief_content: dict[str, Any],
    evidence_refs: list[EvidenceRef],
    *,
    available_images: Iterable[dict[str, Any]] = (),
    client: OpenAI | None = None,
    on_stage: Callable[[str], None] | None = None,
    thinking: bool | None = None,
) -> CoursewareAIResult:
    """Design a blueprint in stages, reporting each model round trip through `on_stage`."""

    def notify(stage: str, detail: str | None = None) -> None:
        if on_stage is None:
            return
        if detail is None:
            on_stage(stage)
            return
        # 兼容只接受一个参数的旧回调
        try:
            on_stage(stage, detail)
        except TypeError:
            on_stage(stage)

    def complete_json(
        messages: list[dict[str, str]],
        stage: str,
        *,
        lock: threading.Lock | None = None,
    ) -> Any:
        """要一次 JSON 补全并解析；解析不了就再要一次，仍不行返回 None。

        实测 deepseek 会偶尔返回**被截断的 JSON**（报 "Expecting ',' delimiter"，
        正文在 15KB 附近断掉）。这条流水线里只有"审校"对这种情况有兜底，骨架、填教案、
        逐页讲稿都是直接抛 —— 异常一路冒到教师面前变成"生成教学蓝图时出错，请重试"，
        前面几分钟的生成全部作废。这里统一成：解析不了就再要一次；仍然不行就返回
        None，交给各自的兜底接手（回落骨架内容 / 拼装修复 / 明确的错误码）。
        """
        attempt_messages = messages
        for attempt in range(_JSON_ATTEMPTS):
            response = complete(attempt_messages)
            if lock is None:
                _add_usage(total_usage, response)
            else:
                # 逐页讲稿是并发跑的，账单累加必须串行
                with lock:
                    _add_usage(total_usage, response)
            try:
                return _extract_json(response.choices[0].message.content or "")
            except json.JSONDecodeError as exc:
                truncated = _finish_reason(response) == "length"
                logger.warning(
                    "[agentic] %s 第 %s/%s 次返回的 JSON 无法解析（被截断=%s）：%s",
                    stage,
                    attempt + 1,
                    _JSON_ATTEMPTS,
                    truncated,
                    exc,
                )
                if truncated:
                    # 原样重发通常会在同一处再截断：要求条目数不变、只把每条写紧凑
                    attempt_messages = [
                        *messages,
                        {
                            "role": "user",
                            "content": (
                                "上一次输出因长度限制被截断，不是合法 JSON。请重新输出**完整且"
                                "可解析**的 JSON：条目数量与结构保持不变，只把每条内容写得更紧凑。"
                            ),
                        },
                    ]
        return None

    if client is None:
        if not settings.deepseek_api_key:
            raise CoursewareAIError(
                "文本 AI 服务尚未配置，无法生成 AI 教学蓝图",
                code="AI_NOT_CONFIGURED",
                recoverable=False,
            )
        client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)

    complete = _make_completer(client, thinking)
    images = [dict(item) for item in available_images]
    allowed_image_ids = {str(item.get("material_id") or "") for item in images} - {""}
    brief = normalize_content(brief_content)
    evidence = [item.model_dump(mode="json") for item in evidence_refs]
    subject, grade = brief.get("subject"), brief.get("grade")
    # brief 一路传下去：四段流水线里的每一段都要看得到教师的明确要求，否则
    # "填充讲稿"那一步根本不知道自己必须避开什么、必须包含什么。
    skeleton_prompt = build_skeleton_prompt(subject, grade, brief)
    teaching_prompt = build_fill_teaching_prompt(subject, grade, brief)
    slides_prompt = build_fill_slides_prompt(subject, brief)
    review_prompt = build_review_prompt(subject, brief)
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    # 1) 搭骨架
    notify("skeleton")
    skeleton_payload = {
        "teaching_brief": brief,
        "evidence": evidence,
        "available_images": images,
    }
    raw = complete_json(
        [
            {"role": "system", "content": skeleton_prompt},
            {"role": "user", "content": json.dumps(skeleton_payload, ensure_ascii=False)},
        ],
        "skeleton",
    )
    if isinstance(raw, dict):
        _log_thinking("skeleton", raw.get("analysis"))
        skeleton = raw.get("skeleton") or raw
    else:
        skeleton = raw

    if not isinstance(skeleton, dict) or not isinstance(skeleton.get("slides"), list) or not skeleton["slides"]:
        # 结构不符合预期：给一次修复机会（不再回传被截断的长文本，避免重复截断）
        notify("repair")
        repair_payload = dict(skeleton_payload)
        repair_payload["task"] = "上一次返回不是合法的骨架 JSON，请只返回骨架 JSON。"
        raw = complete_json(
            [
                {"role": "system", "content": skeleton_prompt},
                {"role": "user", "content": json.dumps(repair_payload, ensure_ascii=False)},
            ],
            "skeleton",
        )
        skeleton = (raw.get("skeleton") if isinstance(raw, dict) else None) or raw
        if not isinstance(skeleton, dict) or not isinstance(skeleton.get("slides"), list) or not skeleton["slides"]:
            raise CoursewareAIError("AI 未能生成课件骨架", code="AI_STRUCTURE_INVALID")

    # 2) 填充教案四项与四类成果设定
    notify("fill_teaching")
    teaching_payload = {
        "teaching_brief": brief,
        "evidence": evidence,
        "duration_minutes": brief.get("duration_minutes"),
        "lesson_sections": skeleton.get("lesson_sections") or [],
    }
    teaching_raw = complete_json(
        [
            {"role": "system", "content": teaching_prompt},
            {"role": "user", "content": json.dumps(teaching_payload, ensure_ascii=False)},
        ],
        "fill_teaching",
    )
    teaching = teaching_raw if isinstance(teaching_raw, dict) else {}
    _log_thinking("fill_teaching", teaching.get("plan_notes"))
    filled_sections = teaching.get("lesson_sections") or skeleton.get("lesson_sections") or []
    output_specs = teaching.get("output_specs") or {}
    if not isinstance(filled_sections, list) or not filled_sections:
        logger.warning("教案填充无效，回落到骨架给出的环节")
        filled_sections = skeleton.get("lesson_sections") or []
    # 填充步骤只被要求"写全四项"，title 与时长仍以骨架为准（同 slides 的合并方式）
    filled_sections = _merge_section_baselines(skeleton.get("lesson_sections"), filled_sections)

    # 3) 分批填充每页要点正文与讲稿
    #
    # 这些批次彼此独立（各写各的页），所以并发发出：页级调用是整条链路上调用次数
    # 最多的一段，串行时 12 页就是 12 次几十秒的往返，教师看到进度条长时间不动，
    # 会以为"跑不了了"。并发后墙钟时间按并发数缩短，结果按批号顺序合并。
    skeleton_slides = [slide for slide in skeleton["slides"] if isinstance(slide, dict)]
    filled_by_order: dict[Any, dict[str, Any]] = {}
    batches = _batched(skeleton_slides, FILL_SLIDES_BATCH)
    total_batches = len(batches)
    results: dict[int, dict[Any, dict[str, Any]]] = {}
    state_lock = threading.Lock()

    def fill_one_batch(index: int, batch: list[dict[str, Any]]) -> None:
        payload = {
            "teaching_brief": brief,
            "slides": [
                {
                    "order": slide.get("order"),
                    "layout": slide.get("layout"),
                    "title": slide.get("title"),
                    "purpose": slide.get("purpose"),
                    "bullets": slide.get("bullets") or [],
                }
                for slide in batch
            ],
        }
        batch_raw = complete_json(
            [
                {"role": "system", "content": slides_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "fill_slides",
            # 这批是并发跑的：账单累加与结果落表都要串行
            lock=state_lock,
        )
        if isinstance(batch_raw, dict):
            _log_thinking("fill_slides", batch_raw.get("draft_notes"))
            items = batch_raw.get("slides") or []
        else:
            items = batch_raw if isinstance(batch_raw, list) else []
        # 模型不一定把 order 原样带回来（实测用 slide_id，格式还每批不同）。
        # 先按编号配对；编号体系完全对不上时，再按批次内顺序补齐 —— 这一批的条目数
        # 与页数一致时，顺序就是最可靠的对应关系。
        batch_orders = [_order_key(slide.get("order")) for slide in batch]
        paired: dict[Any, dict[str, Any]] = {}
        leftovers: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_key = item.get("order")
            if raw_key is None:
                raw_key = item.get("slide_id")
            key = _order_key(raw_key)
            if key in batch_orders and key not in paired:
                paired[key] = item
            else:
                leftovers.append(item)
        free_orders = [key for key in batch_orders if key not in paired]
        if leftovers and free_orders:
            logger.warning(
                "[agentic] fill_slides 有 %s 条结果编号无法识别（模型给的 slide_id=%r…），"
                "已按批次内顺序配对",
                len(leftovers),
                leftovers[0].get("slide_id"),
            )
            for key, item in zip(free_orders, leftovers):
                paired[key] = item
        logger.info(
            "[agentic] fill_slides 第 %s/%s 批返回 %s 条，配对 %s/%s 页",
            index + 1,
            total_batches,
            len(items),
            len(paired),
            len(batch_orders),
        )
        with state_lock:
            results[index] = paired
            ended = len(results)
        # 把"第几页/共几页"报给前端：这一段最慢，没有细分的话进度条像卡死
        notify("fill_slides", f"正在写每页要点与讲稿（{ended}/{total_batches} 页）")

    if batches:
        notify("fill_slides", f"正在写每页要点与讲稿（0/{total_batches} 页）")
    with ThreadPoolExecutor(
        max_workers=min(FILL_SLIDES_CONCURRENCY, total_batches) or 1,
        thread_name_prefix="fill-slides",
    ) as pool:
        list(pool.map(lambda pair: fill_one_batch(*pair), enumerate(batches)))

    for index in range(total_batches):
        filled_by_order.update(results.get(index) or {})

    filled_hits = sum(
        1
        for slide in skeleton_slides
        if (filled_by_order.get(_order_key(slide.get("order"))) or {}).get("speaker_notes")
    )
    logger.info("[agentic] 讲稿填充命中 %s/%s 页", filled_hits, len(skeleton_slides))
    if skeleton_slides and filled_hits < len(skeleton_slides):
        logger.warning(
            "有 %s 页没拿到填充内容，已退回骨架草案（讲稿为空会导致整册校验失败）",
            len(skeleton_slides) - filled_hits,
        )

    slides = []
    for slide in skeleton_slides:
        filled = filled_by_order.get(_order_key(slide.get("order"))) or {}
        slides.append(
            {
                "order": slide.get("order"),
                "title": slide.get("title") or "",
                "purpose": slide.get("purpose") or "",
                "layout": slide.get("layout") or "bullets",
                "bullets": filled.get("bullets") or slide.get("bullets") or [],
                "speaker_notes": filled.get("speaker_notes") or "",
                "image": slide.get("image"),
                "evidence_refs": [],
            }
        )

    assembled = {
        "title": skeleton.get("title") or brief.get("course_name") or brief.get("teaching_goal") or "教学课件",
        "slides": slides,
        "lesson_sections": filled_sections,
        "interactions": skeleton.get("interactions") or [],
        # 教具在"填教案与成果"那一步一起写出来：它属于内容，不需要单独一次模型往返
        "interactive_tools": teaching.get("interactive_tools") or [],
        "output_specs": output_specs,
        "evidence_refs": [],
    }

    # 2.5) 互动教具先就地归一化一次：被拒的条目不再静默消失，而是带着中文原因进入
    # 一轮专门修复。万有引力事故（读数公式引用了未声明的 G、M，教具被整件丢弃，
    # 导出 HTML 退回纯做题页且教师毫不知情）就发生在这里：现在它会先修，修不好
    # 也会写进生成说明；scene 引擎里写坏的单个实体同样只有它自己被移除。
    raw_tools = (
        assembled["interactive_tools"] if isinstance(assembled["interactive_tools"], list) else []
    )
    tool_report: list[dict[str, Any]] = []
    # 只为拿到拒绝报告；正式归一化在修复合并之后再走一次
    _normalize_tools(raw_tools, report=tool_report)
    tool_note = ""
    if tool_report:
        notify("repair", f"正在修复互动教具（{len(tool_report)} 处未通过校验）")
        repair_patch = None
        try:
            repair_patch = complete_json(
                _tool_repair_messages(raw_tools, tool_report, teaching_prompt),
                "repair",
            )
        except (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError) as repair_error:
            logger.warning("[agentic] 教具修复请求失败，保留原本可用的教具：%s", repair_error)
        merged_tools = _merge_repaired_tools(raw_tools, tool_report, _repaired_tools(repair_patch))
        final_report: list[dict[str, Any]] = []
        final_tools = _normalize_tools(merged_tools, report=final_report)
        if final_report:
            logger.warning("[agentic] 教具自动修复后仍有 %s 处未通过校验", len(final_report))
        if final_report and not final_tools:
            # 修不好就明确按无教具生成（回落到互动题），并让教师知道为什么
            assembled["interactive_tools"] = []
            tool_note = "互动教具未通过结构校验，本课按无教具的互动页面生成。"
        else:
            assembled["interactive_tools"] = merged_tools
            if not final_report:
                tool_note = f"互动教具的 {len(tool_report)} 处结构问题已自动修复。"
            else:
                tool_note = (
                    f"互动教具有 {len(final_report)} 处结构问题未能修复（已按降级或移除处理），"
                    f"本课保留 {len(final_tools)} 个可用的动手探究教具。"
                )
    try:
        spec = _normalize_and_validate(
            assembled,
            brief_content=brief_content,
            evidence_refs=evidence_refs,
            allowed_image_ids=allowed_image_ids,
        )
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as assemble_error:
        # 各段分别校验通过、拼成一册仍可能违反整册约束（必填字段、页数、互动答案…）。
        # 这一步以前没有保护，裸异常会冒泡出流水线：它既不是 CoursewareAIError（模板
        # 兜底失效），又会被 SSE 编码吞掉（教师只看到一句"生成失败"）。这里记下明细、
        # 修一次，仍不行就包成业务错误，让上层按 AI_INVALID_RESPONSE 正常降级。
        logger.warning(
            "流水线拼装未通过整册校验，尝试修复一次：%s | %s",
            _validation_summary(assemble_error),
            assemble_error,
        )
        notify("repair")
        missing_notes = _missing_speaker_notes(assembled)
        if missing_notes:
            # 最窄的修复：只要缺的那几页讲稿。整册重发在页数多时会被输出上限截断
            # （实测 13 页的整册 JSON 断在半路，修复反而变成新的失败点）。
            missing = {_order_key(order) for order in missing_notes}
            repair_request = {
                "task": "下面这些页还缺 speaker_notes。只补写讲稿，不要改动其他内容，不要返回整册。",
                "format": '{"slides": [{"order": 页码, "speaker_notes": "……"}]}',
                "teaching_brief": brief,
                "slides": [
                    {
                        "order": slide.get("order"),
                        "title": slide.get("title"),
                        "purpose": slide.get("purpose"),
                        "bullets": slide.get("bullets"),
                    }
                    for slide in assembled["slides"]
                    if isinstance(slide, dict) and _order_key(slide.get("order")) in missing
                ],
            }
            # 讲稿本来就由"写正文"那一步负责，用它自己的提示词最贴任务
            repair_system = slides_prompt
        else:
            repair_request = {
                "task": (
                    "下面这几块内容未通过结构校验。只返回需要修复的部分，格式形如 "
                    '{"lesson_sections": [{"order": 页码, "assessment": "…"}], '
                    '"interactions": [...], "output_specs": {...}}；'
                    "不要重复返回已经正确的内容，也不要返回整册蓝图。"
                ),
                "validation_error": _validation_summary(assemble_error),
                "offending": _offending_slices(assembled, assemble_error),
                "teaching_brief": brief,
                "evidence": evidence,
            }
            # 骨架提示词明确要求"不要写讲稿"，拿它修整册只会越修越坏
            repair_system = build_generation_prompt(subject, grade, brief)
        logger.info(
            "[agentic] 修复请求：%s",
            f"补 {len(repair_request['slides'])} 页讲稿" if missing_notes else "按校验错误局部修复",
        )
        patch = complete_json(
            [
                {"role": "system", "content": repair_system},
                {"role": "user", "content": json.dumps(repair_request, ensure_ascii=False)},
            ],
            "repair",
        )
        if patch is None or not _merge_blueprint_patch(assembled, patch):
            logger.warning("修复响应里没有可用的补丁，无法继续")
            raise CoursewareAIError(
                "AI 生成的教学蓝图未通过结构校验",
                code="AI_INVALID_RESPONSE",
            ) from assemble_error
        try:
            # 补丁已并回，重新走整册校验（形状归一化会再跑一遍）
            spec = _normalize_and_validate(
                assembled,
                brief_content=brief_content,
                evidence_refs=evidence_refs,
                allowed_image_ids=allowed_image_ids,
            )
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as final_error:
            logger.warning(
                "流水线拼装修复后仍未通过校验：%s | %s",
                _validation_summary(final_error),
                final_error,
            )
            raise CoursewareAIError(
                "AI 生成的教学蓝图未通过结构校验",
                code="AI_INVALID_RESPONSE",
            ) from final_error
    spec.generation_notes.append("采用骨架→填充→审校的分段生成，教案与成果设定单独成段撰写。")

    # 4) 审校：与经典模式同一套规则，先让模型列出发现，再给修正后的完整 JSON
    notify("review")
    candidate_spec = spec
    review_payload = {
        "teaching_brief": brief,
        "evidence": evidence,
        "candidate": spec.model_dump(mode="json"),
    }
    try:
        review_response = complete(
            [
                {"role": "system", "content": review_prompt},
                {"role": "user", "content": json.dumps(review_payload, ensure_ascii=False)},
            ],
        )
        _add_usage(total_usage, review_response)
        review_raw = _extract_json(review_response.choices[0].message.content or "")
        if isinstance(review_raw, dict):
            _log_thinking("review", review_raw.get("findings"))
            reviewed = review_raw.get("blueprint") or review_raw
        else:
            reviewed = review_raw
        spec = _normalize_and_validate(
            reviewed,
            brief_content=brief_content,
            evidence_refs=evidence_refs,
            allowed_image_ids=allowed_image_ids,
        )
        spec.generation_notes.append("教学事实和互动可判定性已通过独立 AI 审校。")
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as review_error:
        logger.warning("流水线审校未通过校验，保留已校验的候选蓝图：%s", review_error)
        spec.generation_notes.append("AI 审校响应结构无效，已保留通过校验的生成蓝图。")
    except RateLimitError:
        logger.warning("流水线审校触发限流，保留已校验的候选蓝图")
        spec.generation_notes.append("AI 审校触发限流，已保留通过校验的生成蓝图。")

    spec = _carry_interactive_tools(spec, candidate_spec)
    if tool_note:
        spec.generation_notes.append(tool_note)
    return CoursewareAIResult(
        spec=spec,
        model_name=MODEL_NAME,
        prompt_version=AGENTIC_PROMPT_VERSION,
        usage=total_usage,
    )
