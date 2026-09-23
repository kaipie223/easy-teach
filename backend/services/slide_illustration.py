"""为课件页面自动配图。

一份课件十几页，让教师逐页选图太慢；但"每配一张图就新建一个成果版本"又会把版本
历史冲垮。所以这里的做法是：

1. 生成课件时在**渲染产物之前**配好图，图片随第一次生成写进同一个版本；
2. 给已有版本补配图时，整批只产生一个新版本（不是一页一个版本）；
3. 单页失败只跳过该页 —— 一张插图生成不出来，不该让整份课件生成失败。

图片与手工上传走同一套资料结构，所以之后"换图 / 移除配图 / 改位置"用的还是既有
的成果编辑接口，不需要第二套逻辑。
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from sqlalchemy.orm import Session as DBSession

from backend.config import settings
from backend.core.errors import ApiError
from backend.models.material import Material, MaterialAnalysis
from backend.models.project import Project
from backend.services.image_generation import (
    GeneratedImage,
    ImageGenerationError,
    generate_image,
    image_generation_enabled,
)
# 有效版式与"哪些版式容不下配图"都住在渲染器里：这里是它们唯一的事实来源，
# 在配图侧另抄一份判断，迟早会和导出结果对不上。
from backend.services.generator import TEXT_ONLY_LAYOUTS, _slide_layout as resolve_slide_layout
from backend.services.limits import consume_model_quota, ensure_storage_capacity
from backend.services.materials import (
    ParsedChunk,
    ParsedMaterial,
    apply_parsed_material,
    fail_material_analysis,
    parser_identity,
)
from backend.services.uploads import checksum_file

logger = logging.getLogger(__name__)

# 自动配图统一放右半页：正文留在上层可读，图不会被白色蒙层压平，
# 也不会像整页图那样把要点挤进讲稿。教师不满意可以逐页换位置。
AUTO_PLACEMENT = "right"

# 画面里不出现文字：插图上的"字"会与页面上真正的文字打架。
STYLE_SUFFIX = "扁平化教学插画，简洁明快，主体明确，留白充足，画面中不要出现任何文字"


def illustration_prompt(slide: dict[str, Any]) -> str:
    """从页面内容拼出一句配图提示词。

    用标题、教学目的和前两条要点做主体，让插图跟着内容走；页面上没有可用信息时
    退回一个通用教学场景，而不是让提示词为空导致生成被拒。
    """
    bullets = [str(item).strip() for item in (slide.get("bullets") or []) if str(item).strip()]
    parts = [
        str(slide.get("title") or "").strip(),
        str(slide.get("purpose") or "").strip(),
        bullets[0] if bullets else "",
        bullets[1] if len(bullets) > 1 else "",
    ]
    subject = "；".join(part for part in parts if part) or "课堂教学场景"
    return f"{subject}。{STYLE_SUFFIX}"


def slides_missing_image(slides: Sequence[Any]) -> list[dict[str, Any]]:
    """还没有配图的页面。人工选的图优先级最高，永远不覆盖。"""
    return [
        slide
        for slide in slides
        if isinstance(slide, dict) and not str((slide.get("image") or {}).get("material_id") or "")
    ]


def split_illustratable(slides: Sequence[Any]) -> tuple[list[dict[str, Any]], int]:
    """分成"(可以自动配图的页面, 因结构版式跳过的页面数)"。

    结构版式（目录/卡片/流程/对比/大数字/引用/小结）加配图后会整页退回普通要点页：
    渲染器宁可牺牲版式也不丢图，这是对的，但对"每页自动配图"来说等于拿版面换插图。
    所以这些页面留白，教师想配就单独配。

    有效版式必须问渲染器自己的规则：版式名缺失时首页是封面、末页会变成小结页，
    在这里另写一套判断必然与导出一致不了。
    """
    pending: list[dict[str, Any]] = []
    skipped = 0
    total = len(slides)
    for index, slide in enumerate(slides):
        if not isinstance(slide, dict):
            continue
        if str((slide.get("image") or {}).get("material_id") or ""):
            continue
        if resolve_slide_layout(slide, index, total) in TEXT_ONLY_LAYOUTS:
            skipped += 1
            continue
        pending.append(slide)
    return pending, skipped


def store_generated_image(
    db: DBSession,
    project: Project,
    *,
    user_id: str,
    generated: GeneratedImage,
    prompt: str,
) -> Material:
    """把一张生成图登记成项目图片资料，返回该资料。

    单张"AI 生成配图"接口与批量自动配图共用这一处实现：两条路径产出的资料必须
    完全一致，否则下游（检索、讲义配图附录、换图）就会分叉。
    """
    material_id = f"mat_{uuid.uuid4().hex[:24]}"
    safe_name = f"ai_image_{material_id.rsplit('_', 1)[-1]}{generated.extension}"
    stored_path = settings.upload_dir / material_id / safe_name
    ensure_storage_capacity(db, user_id, len(generated.data))
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_bytes(generated.data)
    checksum, size_bytes = checksum_file(stored_path)

    now = datetime.now(timezone.utc)
    prompt_text = prompt.strip()
    material = Material(
        material_id=material_id,
        owner_id=user_id,
        project_id=project.project_id,
        session_id=None,
        original_name=safe_name,
        file_type="image",
        mime_type=generated.mime_type,
        stored_path=str(stored_path),
        size_bytes=size_bytes,
        checksum_sha256=checksum,
        status="processing",
        ref_description=f"AI 生成配图：{prompt_text[:120]}",
        created_at=now,
        updated_at=now,
    )
    parser_name, parser_version = parser_identity("image")
    analysis = MaterialAnalysis(
        analysis_id=f"analysis_{uuid.uuid4().hex[:24]}",
        material_id=material_id,
        run_number=1,
        parser_name=parser_name,
        parser_version=parser_version,
        status="processing",
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add_all([material, analysis])
    db.commit()

    # 生成图不做 OCR/视觉解析：它的内容来自提示词本身。直接登记一条已完成的分析，
    # 让这张图和其它图片资料在下游（检索、讲义配图附录）表现一致。
    parsed = ParsedMaterial(
        text_content=f"AI 生成配图：{prompt_text}",
        chunks=[
            ParsedChunk(
                text=f"AI 生成配图：{prompt_text}",
                locator={"source": "ai_generated_image"},
                metadata={
                    "kind": "ai_generated_image",
                    "prompt": prompt_text,
                    "model": settings.ark_image_model,
                },
            )
        ],
        result_json={
            "kind": "ai_generated_image",
            "prompt": prompt_text,
            "model": settings.ark_image_model,
            "mime_type": generated.mime_type,
        },
    )
    try:
        apply_parsed_material(db, material, analysis, parsed)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - 登记失败要留下可诊断的失败记录
        db.rollback()
        material = db.query(Material).filter(Material.material_id == material_id).one()
        analysis = (
            db.query(MaterialAnalysis)
            .filter(MaterialAnalysis.analysis_id == analysis.analysis_id)
            .one()
        )
        fail_material_analysis(db, material, analysis, exc)
        db.commit()
        raise ApiError(
            "配图已生成但登记失败，请重试",
            code="IMAGE_REGISTRATION_FAILED",
            status_code=500,
            details={"material_id": material_id},
        ) from exc
    return material


def illustrate_slides(
    db: DBSession,
    project: Project,
    slides: list[dict[str, Any]],
    *,
    user_id: str,
    limit: int | None = None,
    concurrency: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[str]:
    """给缺图页各生成一张配图，原地写入 ``slide["image"]``，返回新配的 material_id。

    并发生成、串行入库：`DBSession` 不是线程安全的，所以所有写库动作都留在调用线程，
    工作线程只负责那次 HTTP 调用。
    """
    if not image_generation_enabled():
        logger.info("跳过自动配图：未配置图像生成服务")
        return []

    pending, skipped_layouts = split_illustratable(slides)
    if skipped_layouts:
        logger.info("跳过 %s 页结构版式页：它们自带版面，加图会被退回普通要点页", skipped_layouts)
    if not pending:
        return []
    cap = max(int(limit if limit is not None else settings.slide_illustration_limit), 0)
    pending = pending[:cap]
    total = len(pending)
    workers = max(
        1,
        min(
            int(concurrency if concurrency is not None else settings.slide_illustration_concurrency),
            total,
        ),
    )
    if on_progress is not None:
        on_progress(0, total)

    jobs: dict[Any, tuple[dict[str, Any], str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for slide in pending:
            prompt = illustration_prompt(slide)
            try:
                # 额度按张计：一张插图 = 一次模型请求，配额用尽就停在当前页。
                consume_model_quota(user_id)
            except ApiError as exc:
                logger.warning("停止自动配图：%s", exc)
                break
            jobs[pool.submit(generate_image, prompt)] = (slide, prompt)

        generated: list[tuple[dict[str, Any], str, GeneratedImage]] = []
        for future in as_completed(jobs):
            slide, prompt = jobs[future]
            try:
                generated.append((slide, prompt, future.result()))
            except ImageGenerationError as exc:
                logger.warning("第 %s 页配图生成失败，已跳过：%s", slide.get("order"), exc)
            except Exception as exc:  # noqa: BLE001 - 单页异常不该中断整批
                logger.warning("第 %s 页配图生成异常，已跳过：%s", slide.get("order"), exc)

    material_ids: list[str] = []
    finished = 0
    for slide, prompt, image in generated:
        try:
            material = store_generated_image(
                db, project, user_id=user_id, generated=image, prompt=prompt
            )
        except ApiError as exc:
            logger.warning("第 %s 页配图登记失败，已跳过：%s", slide.get("order"), exc)
            finished += 1
            if on_progress is not None:
                on_progress(finished, total)
            continue
        slide["image"] = {
            "material_id": material.material_id,
            "placement": AUTO_PLACEMENT,
            "caption": "",
        }
        material_ids.append(material.material_id)
        finished += 1
        if on_progress is not None:
            on_progress(finished, total)
    return material_ids
