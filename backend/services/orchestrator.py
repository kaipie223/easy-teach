"""核心编排器 — 串联意图理解 → RAG → 资料解析 → 课件生成的全流程。

这是整个系统的核心调度模块，被 routers/ 调用。
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, Callable

from sqlalchemy.orm import Session as DBSession

from backend.config import settings
from backend.models.courseware import CoursewarePlan
from backend.models.project import Project
from backend.models.session import Session
from backend.models.task import Task
from backend.models.versioning import ArtifactVersion
from backend.schemas import (
    ChatEvent,
    GenerationInstruction,
    IntentResult,
    MessageType,
    OutputFile,
    ReferenceMaterial,
    TaskInfo,
)
from backend.services.ai_stream import aiter_blocking_generator
from backend.services.intent import get_intent_analyzer
from backend.services.brief import (
    CORE_FIELD_ORDER,
    FIELD_LABELS,
    MAX_ASKS_PER_FIELD,
    TEACHER_CONFIRM_FIELDS,
    apply_teacher_answer,
    brief_context_block,
    brief_to_intent,
    capture_teacher_answer,
    ensure_draft,
    get_confirmed_brief,
    infer_grade,
    get_latest_brief,
    is_filled,
    missing_fields,
    next_question,
    pending_core_fields,
    normalize_content,
    persist_intent_result,
    proposal_option,
    remember_asked_field,
    to_info,
)
from backend.services.intent import IntentServiceError
from backend.services.courseware import build_courseware_plan
from backend.services.rag import search_sync
from backend.services.quality import require_courseware_quality
from backend.services.task_queue import claim_task_attempt, task_info_values, touch_task, utcnow
from backend.services.versions import ensure_initial_version
from backend.services.parser import parse_docx, parse_pdf
from backend.services.generator import generate_docx, generate_html, generate_pptx
from backend.services.materials import resolve_slide_images
from backend.services.limits import ensure_storage_capacity, ensure_task_capacity
from backend.services.progress import GENERATION_STAGES, percent_of
from backend.services.slide_illustration import illustrate_slides
from backend.services.uploads import remove_managed_file

logger = logging.getLogger(__name__)


def _illustration_progress(db: DBSession, task: Task, done: int, total: int) -> None:
    """把自动配图的按页进度写进任务。

    配图是这条链路上唯一按页计数的步骤，"还剩几页"比一个百分比更具体，所以这里直接
    更新阶段文案；百分比仍取阶段表中的值，进度条不会因此来回跳。
    """
    if total <= 0:
        return
    task.stage = "illustrate"
    task.stage_label = f"为第 {min(done + 1, total)}/{total} 页生成配图"
    task.progress = percent_of(GENERATION_STAGES, "illustrate")
    task.updated_at = utcnow()
    db.commit()


class Orchestrator:
    """编排器单例。"""

    def __init__(self):
        self.intent_analyzer = get_intent_analyzer()

    # ── 对话 ──────────────────────────────────────────

    async def chat(
        self,
        session_id: str,
        message: str,
        *,
        history: list[dict] | None = None,
        db: DBSession | None = None,
        session: Session | None = None,
        opening: bool = False,
        skip_field: str | None = None,
    ) -> AsyncGenerator[ChatEvent, None]:
        """处理一轮对话，SSE 流式返回事件。

        ``opening`` 为真表示这是新会话的自动开场（老师还没说过话）：此时不逐项逼问，
        只邀请老师把想法说出来，见下面 QUESTION 分支的说明。

        ``skip_field`` 为真表示本轮是"这项先跳过"：该字段按默认处理、不再追问，
        且那一句跳过的话绝不会被当成答案写进需求单。
        """
        messages = history or [{"role": "user", "content": message}]

        # 让模型看到"已经确认了什么、还缺什么"：只看聊天历史，对话一长就会漏问、
        # 重问、顺序乱。待问清单与确认校验共用同一套判定（brief.pending_core_fields）。
        context_block = ""
        prior_content = None
        if db is not None and session is not None:
            prior_brief = get_latest_brief(
                db, project_id=session.project_id, session_id=session.session_id
            )
            context_block = brief_context_block(prior_brief)
            if prior_brief is not None:
                # persist_intent_result 稍后会原地改写这份草稿；先拍快照，用于判断
                # "教师这轮的回答有没有真的落到被问的那一项上"。
                prior_content = normalize_content(prior_brief.content_json)

        # 1. 流式调用意图分析：模型产出的教师可见文本边生成边推送。
        #    The generator is drained on a worker thread so the event loop keeps
        #    serving other requests while the model is still writing.
        result = None
        reply = None
        try:
            async for kind, value in aiter_blocking_generator(
                self.intent_analyzer.analyze_stream(session_id, messages, context_block)
            ):
                if kind == "text":
                    yield ChatEvent(event_type=MessageType.DELTA, content=value)
                else:
                    result, reply = value
        except IntentServiceError as exc:
            # 实测 deepseek-v4-flash 会偶尔完全无视 JSON 模式、返回一句纯文本。以前
            # 这一轮连同教师刚给的回答一起丢掉，于是同一个问题被反复问、每轮还要白等
            # 两次重试。改为：把回答补进需求单，再用后端准备好的问题问下一项。
            if db is None or session is None or exc.code != "AI_INVALID_RESPONSE":
                raise
            logger.warning("模型本轮未返回结构化结果，按当前进度继续：%s", exc)
            async for event in self._recover_stalled_turn(db, session, message):
                yield event
            return
        if result is None:
            raise RuntimeError("意图分析未返回结果")

        # 模型本轮的原始产出要先留一份：persist_intent_result 会把 result 换成按需求单
        # 回写后的"规范化结果"，模型这轮没给追问时里面会残留上一轮的旧问题 —— 措辞与
        # 候选答案必须用本轮原始的，否则会出现"旧问题配新按钮"。
        model_question = str(result.follow_up_question or "").strip()
        model_options = [
            str(item).strip() for item in (result.options or []) if str(item).strip()
        ][:5]

        brief = None
        if db is not None and session is not None:
            brief, result = persist_intent_result(db, session, result)
        brief_content = normalize_content(brief.content_json) if brief is not None else None

        # "这项先跳过"：把该项标成按默认处理 —— 不再追问（计数直接顶到上限）、不算
        # 待确认，所以需求单的进度会当场往前走一格。关键是**绝不能把这句话当成答案**：
        # 老实现只发"跳过"两个字，而 _answer_fits 对多数文本字段一律放行，于是"跳过"
        # 会被写进教学目标/授课对象这类字段里，污染需求单。
        if brief is not None and skip_field:
            skipped = normalize_content(brief.content_json)
            counts = dict(skipped.get("ask_counts") or {})
            counts[skip_field] = MAX_ASKS_PER_FIELD
            skipped["ask_counts"] = counts
            if str(skipped.get("asking_field") or "") == skip_field:
                skipped["asking_field"] = ""
            brief.content_json = skipped
            brief.source_refs = {**(brief.source_refs or {}), skip_field: "default"}
            db.flush()
            brief_content = normalize_content(brief.content_json)

        # 模型返回成功 ≠ 教师的回答被记下来了。实测过：教师答"大学"，模型把信息
        # 写进 target_audience，grade 字段仍为空 → 待问清单永远是 grade →
        # "不管说什么都在问学段"。这里补两步：①把上一轮问过的字段按教师原话补上
        # ②仍为空则从已确认上下文推断学段。
        if brief is not None:
            refs = dict(brief.source_refs or {})
            asked_last = str(brief_content.get("asking_field") or "")
            changed = False
            # skip_field 那一轮不补写答案：这一轮的正文是"先跳过"，不是回答
            if asked_last and not skip_field and capture_teacher_answer(
                brief_content, refs, asked_last, message
            ):
                changed = True
            # 来源有讲究的字段必须由教师确认过才算数：设计类字段（目标/重难点/产出…）
            # 模型填的只是提议；课时带着默认值 45，也只有 teacher/ai 定过才算数
            # （见 pending_core_fields）。教师这一轮的回答让该项的值真的落了地 ——
            # 模型抽出了新值，或教师直接点了上面的按钮 —— 就记 teacher；只是顺手
            # 聊了句别的、值原样不动时不算：宁可再问一次，也不能替教师拍板。
            if (
                asked_last
                and not skip_field
                and (asked_last in TEACHER_CONFIRM_FIELDS or asked_last == "duration_minutes")
                and str(refs.get(asked_last) or "").lower() not in {"teacher", "default"}
                and is_filled(brief_content.get(asked_last))
            ):
                prev_value = (prior_content or {}).get(asked_last)
                offered = [
                    str(item).strip() for item in (prior_content or {}).get("options") or []
                ]
                proposal_seen = proposal_option(prior_content, asked_last) if prior_content else ""
                if proposal_seen:
                    offered.append(proposal_seen)
                if (
                    prev_value != brief_content.get(asked_last)
                    or str(message).strip() in offered
                ):
                    refs[asked_last] = "teacher"
                    changed = True
            if not str(brief_content.get("grade") or "").strip():
                inferred = infer_grade(brief_content)
                if inferred:
                    brief_content["grade"] = inferred
                    refs["grade"] = "inferred"
                    changed = True
            if changed:
                brief.content_json = brief_content
                brief.source_refs = refs
                db.flush()
        refs = dict(brief.source_refs if brief is not None else {} or {})
        brief_payload = to_info(brief).model_dump(mode="json") if brief is not None else None

        # 2. 输出结构化结果；正文优先复用已流式展示的 reply，避免两处文案不一致。
        #
        # 唯一事实来源是需求单状态。next_question 返回 None 只有两种情形 —— 信息已齐，
        # 或剩下的项都已经问满上限 —— 两者都该收敛到确认总结。**绝不能退回模型自己的
        # 问题与选项**：模型看到字段仍为空，会把同一个问题再问一遍，于是又变成
        # "不管说什么都在问学段"（这条路径实测复现过）。
        target = next_question(brief_content or {}, refs)
        pending_now = pending_core_fields(brief_content or {}, refs)
        # 进度随事件下发：卡片显示"第几项 / 还剩几项"，需求单显示"已确认 x/y"
        progress = {"pending_fields": pending_now, "core_total": len(CORE_FIELD_ORDER)}

        if opening and target is not None:
            # 开场不逐项逼问：老师还没开口，先请他把想法说出来。以前开场第一张卡就是
            # "哪个学段？"的四选一，看起来像一张表单；而"给大学本科生讲流体力学"这样
            # 一句话里通常已经带了学段，根本不必问。
            invitation = (
                "先说说这节课：讲什么内容、给哪个学段的学生上、打算用多长时间、"
                "最希望学生掌握什么。想到多少说多少，我会边听边把需求单整理出来。"
            )
            yield ChatEvent(
                event_type=MessageType.QUESTION,
                content=invitation,
                data={
                    "prompt": invitation,
                    # 没有候选项：这是邀请，不是选择题
                    "options": [],
                    "field": None,
                    "invite": True,
                    "missing_info": result.missing_info,
                    "allow_free": True,
                    "brief": brief_payload,
                    **progress,
                },
            )
            return

        if target is not None:
            # 信息不全 → 追问。"问哪一项"由需求单状态决定：模型偶尔会去问已经填好的
            # 字段（那就会"同一个问题问好几遍"），状态才是唯一事实来源。而"怎么问、
            # 给哪些按钮"交给模型 —— 只有它看得到对话里的具体课题，能拟出贴合本课的
            # 候选答案；模型这轮没给候选时退回后端的固定问法，对话照样推进。
            field, question, options = target
            if model_options:
                if model_question:
                    question = model_question
                options = list(model_options)
            proposal = proposal_option(brief_content or {}, field)
            if proposal:
                # 该项已有一个待确认的提议值（通常是模型写的）→ 摆成第一个按钮，
                # 教师点一下即算确认，不必重新打字。
                options = [proposal] + [item for item in options if item.strip() != proposal]
            options = options[:5]
            if brief is not None:
                remember_asked_field(db, brief, field)
                db.flush()
            yield ChatEvent(
                event_type=MessageType.QUESTION,
                content=question,
                data={
                    "prompt": question,
                    "missing_info": result.missing_info,
                    # 候选答案渲染成按钮，点一下即作为回答发送
                    "options": options,
                    "field": field,
                    "field_label": FIELD_LABELS.get(field, field),
                    "allow_free": True,
                    "brief": brief_payload,
                    **progress,
                },
            )
        else:
            # 核心字段齐了。但模型这一轮自己提了一个问题（例如"这两课时您更希望我把
            # 重心放在哪一侧"）——那往往是真正影响整份设计的取舍，直接跳到确认面板会
            # 给教师一个自相矛盾的界面：正文在提问，下面却是"确认教学信息"，而那句话
            # 再也没有机会回答。所以放行一次：把这句问题做成卡片，教师答完再进确认。
            # 只放行一次 —— 否则模型每轮都能提个新问题，永远到不了确认那一步。
            # 只在"真的齐了"（没有任何待确认核心字段）时才放行。撞上限而停下的情况不算：
            # 那时模型问的多半还是那个没填上的核心字段，放行等于把"永远问学段"放回来。
            # 用本轮原始的 follow_up_question：回写后的 result 里可能残留上一轮的旧问题。
            refinement = model_question
            if (
                refinement
                and not pending_now
                and not (brief_content or {}).get("refinement_asked")
            ):
                if brief is not None:
                    marked = normalize_content(brief.content_json)
                    marked["refinement_asked"] = True
                    brief.content_json = marked
                    db.flush()
                yield ChatEvent(
                    event_type=MessageType.QUESTION,
                    content=refinement,
                    data={
                        "prompt": refinement,
                        "options": [],
                        "field": None,
                        # 这不是核心字段，没有"跳过这项"一说
                        "refine": True,
                        "allow_free": True,
                        "brief": brief_payload,
                        **progress,
                    },
                )
                return
            # 信息完整 → 确认总结
            yield ChatEvent(
                event_type=MessageType.CONFIRM,
                content=reply or result.confirm_summary or _build_confirm_text(result),
                data={
                    "fields": {
                        "topic": result.teaching_goal,
                        "audience": result.target_audience,
                        "duration": f"{result.duration_minutes} 分钟",
                        "core_knowledge": "、".join(kp.title for kp in result.knowledge_points),
                        "logic_flow": " → ".join(result.logic_flow),
                        "teaching_focus": (brief_content or {}).get("teaching_focus", ""),
                        "teaching_difficulties": (brief_content or {}).get("teaching_difficulties", ""),
                        "output_types": "、".join((brief_content or {}).get("output_types", [])),
                        "interaction_ideas": (brief_content or {}).get("interaction_ideas", ""),
                        "style": result.style_preference,
                    },
                    "note": result.confirm_summary,
                    "brief": brief_payload,
                },
            )

    async def _recover_stalled_turn(
        self,
        db: DBSession,
        session: Session,
        message: str,
    ) -> AsyncGenerator[ChatEvent, None]:
        """模型整轮失败时的兜底：保住教师的回答，并按进度继续追问。

        以前这种情况整轮作废（教师看到"AI 返回的数据格式无效，本次内容没有保存"），
        需求单没有任何变化，于是同一个问题被反复问；而两次重试各要等十几秒，
        体感就是"又慢又重复"。现在把回答写进上一轮问过的字段，再由后端决定下一问。
        """
        brief = ensure_draft(
            db,
            user_id=session.user_id,
            project_id=session.project_id,
            session_id=session.session_id,
        )
        content = normalize_content(brief.content_json)
        refs = dict(brief.source_refs or {})
        asked = str(content.get("asking_field") or "")
        if asked:
            apply_teacher_answer(content, refs, asked, message)
        target = next_question(content, refs)
        content["asking_field"] = target[0] if target else ""
        brief.content_json = content
        brief.source_refs = refs
        db.flush()
        db.commit()
        brief_payload = to_info(brief).model_dump(mode="json")

        if target is not None:
            field, question, options = target
            proposal = proposal_option(content, field)
            if proposal:
                # 模型整轮失败时用它写过的提议值兜底：摆成第一个按钮，点一下即确认
                options = [proposal] + [item for item in options if item.strip() != proposal]
            options = options[:5]
            yield ChatEvent(
                event_type=MessageType.QUESTION,
                content=question,
                data={
                    "prompt": question,
                    "missing_info": missing_fields(content, refs),
                    "options": options,
                    "field": field,
                    "field_label": FIELD_LABELS.get(field, field),
                    "allow_free": True,
                    "brief": brief_payload,
                    "pending_fields": pending_core_fields(content, refs),
                    "core_total": len(CORE_FIELD_ORDER),
                },
            )
            return

        result = brief_to_intent(brief) if brief is not None else None
        note = (result.confirm_summary if result is not None else None) or "信息已经齐了，确认后就可以开始生成。"
        yield ChatEvent(
            event_type=MessageType.CONFIRM,
            content=note,
            data={
                "fields": {
                    "topic": result.teaching_goal if result is not None else "",
                    "audience": result.target_audience if result is not None else "",
                    "duration": f"{result.duration_minutes} 分钟" if result is not None else "",
                    "core_knowledge": "、".join(kp.title for kp in result.knowledge_points)
                    if result is not None
                    else "",
                    "logic_flow": " → ".join(result.logic_flow) if result is not None else "",
                    "teaching_focus": content.get("teaching_focus", ""),
                    "teaching_difficulties": content.get("teaching_difficulties", ""),
                    "output_types": "、".join(content.get("output_types", []) or []),
                    "interaction_ideas": content.get("interaction_ideas", ""),
                    "style": result.style_preference if result is not None else "",
                },
                "note": note,
                "brief": brief_payload,
            },
        )

    # ── 课件生成 ──────────────────────────────────────

    def create_generation_task(
        self,
        session: Session,
        db: DBSession,
        *,
        plan_id: str | None = None,
        artifact_version_id: str | None = None,
        task_type: str = "generation",
        idempotency_key: str | None = None,
    ) -> TaskInfo:
        """创建异步生成任务，写入数据库。"""
        from backend.models.session import gen_id

        normalized_key = idempotency_key.strip() if idempotency_key else None
        if normalized_key:
            existing = (
                db.query(Task)
                .filter(
                    Task.user_id == session.user_id,
                    Task.idempotency_key == normalized_key,
                    Task.task_type == task_type,
                )
                .order_by(Task.created_at.desc())
                .first()
            )
            if existing is not None:
                return TaskInfo(**task_info_values(existing))

        if not session.user_id:
            raise RuntimeError("Authenticated generation requires a session owner")
        ensure_task_capacity(db, session.user_id)

        task_id = gen_id("task")
        task = Task(
            task_id=task_id,
            user_id=session.user_id,
            project_id=session.project_id,
            plan_id=plan_id,
            artifact_version_id=artifact_version_id,
            session_id=session.session_id,
            task_type=task_type,
            status="pending",
            progress=0,
            retry_count=0,
            max_retries=settings.task_max_retries,
            idempotency_key=normalized_key,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return TaskInfo(**task_info_values(task))

    def run_generation(self, task_id: str, *, raise_errors: bool = False) -> None:
        """后台执行课件生成全流程 — 使用独立数据库会话，杜绝跨线程会话泄漏。"""
        from backend.db.database import SessionLocal

        db = SessionLocal()
        try:
            if not claim_task_attempt(db, task_id):
                return
            self._run_generation_impl(task_id, db, raise_errors=raise_errors)
        except Exception:
            logger.exception("Task %s failed", task_id)
            if raise_errors:
                raise
        finally:
            db.close()

    def _run_generation_impl(self, task_id: str, db, *, raise_errors: bool = False) -> None:
        """课件生成的内部实现 — 接收独立会话，不受请求生命周期影响。"""
        task = db.query(Task).filter(Task.task_id == task_id).first()
        if not task:
            return
        if not task.user_id:
            raise RuntimeError("Anonymous generation tasks are no longer supported")

        try:
            touch_task(db, task, "brief")

            # Step 1: 锁定已确认 brief；匿名 M0 会话继续使用旧意图缓存。
            confirmed_brief = (
                get_confirmed_brief(db, project_id=task.project_id)
                if task.project_id
                else None
            )
            intent = (
                brief_to_intent(confirmed_brief)
                if confirmed_brief is not None
                else self.intent_analyzer.lock_intent(task.session_id)
            )
            touch_task(db, task, "search")

            # Step 2: RAG 检索
            query = f"{intent.teaching_goal} {' '.join(kp.title for kp in intent.knowledge_points)}"
            rag_docs = search_sync(query, top_k=5, owner_id=task.user_id)
            touch_task(db, task, "parse")

            # Step 3: 解析参考资料
            references = _load_references(task.session_id, db)
            touch_task(db, task, "plan")

            # Step 4: Compile or load the single source-of-truth blueprint.
            plan = None
            artifact_version = None
            if task.artifact_version_id:
                artifact_version = (
                    db.query(ArtifactVersion)
                    .filter(
                        ArtifactVersion.artifact_version_id == task.artifact_version_id,
                        ArtifactVersion.project_id == task.project_id,
                    )
                    .first()
                )
                if artifact_version is None:
                    raise RuntimeError("绑定的成果版本不存在")
            if task.plan_id:
                plan = (
                    db.query(CoursewarePlan)
                    .filter(
                        CoursewarePlan.plan_id == task.plan_id,
                        CoursewarePlan.project_id == task.project_id,
                    )
                    .first()
                )
            if plan is None and task.project_id and confirmed_brief is not None:
                project = db.query(Project).filter(Project.project_id == task.project_id).first()
                if project is not None:
                    plan = build_courseware_plan(
                        db,
                        project,
                        rag_docs=rag_docs,
                        on_stage=_plan_stage_sink(task_id),
                    )
                    task.plan_id = plan.plan_id
                    artifact_version = ensure_initial_version(
                        db,
                        project,
                        plan,
                        user_id=task.user_id or project.owner_id,
                    )
                    task.artifact_version_id = artifact_version.artifact_version_id
            if plan is not None:
                if artifact_version is None:
                    project = (
                        db.query(Project).filter(Project.project_id == task.project_id).first()
                        if task.project_id
                        else None
                    )
                    if project is not None:
                        artifact_version = ensure_initial_version(
                            db,
                            project,
                            plan,
                            user_id=task.user_id or project.owner_id,
                        )
                        task.artifact_version_id = artifact_version.artifact_version_id
                intent_dict = artifact_version.snapshot_json if artifact_version else plan.plan_json
            else:
                # Legacy anonymous-session compatibility until it has a project plan.
                instruction = GenerationInstruction(
                    session_id=task.session_id,
                    teaching_goal=intent.teaching_goal,
                    target_audience=intent.target_audience,
                    duration_minutes=intent.duration_minutes,
                    knowledge_points=intent.knowledge_points,
                    logic_flow=intent.logic_flow,
                    style_preference=intent.style_preference,
                    rag_context=rag_docs,
                    reference_materials=references,
                    extra_requirements="",
                )
                intent_dict = instruction.model_dump()
            if plan is not None:
                # 带上需求单：质检要能看出"教师要求禁止出现的内容"是否混进了产物。
                confirmed_brief = get_confirmed_brief(db, project_id=plan.project_id)
                quality_report = require_courseware_quality(
                    intent_dict,
                    brief_content=(
                        confirmed_brief.content_json if confirmed_brief is not None else None
                    ),
                )
                if artifact_version is not None:
                    artifact_version.quality_status = quality_report["status"]
                    artifact_version.quality_report = quality_report
                    db.flush()
            touch_task(db, task, "quality")

            # Step 4.5: 每页自动配图。放在质量门禁之后（门禁没过不该先花掉图片额度），
            # 也放在渲染之前 —— 图片随这一版一起写进快照，而不是每配一张图就多一个版本。
            if (
                artifact_version is not None
                and settings.slide_illustration_enabled
                and isinstance(intent_dict.get("slides"), list)
            ):
                illustration_project = (
                    db.query(Project).filter(Project.project_id == task.project_id).first()
                    if task.project_id
                    else None
                )
                if illustration_project is not None:
                    touch_task(db, task, "illustrate")
                    attached = illustrate_slides(
                        db,
                        illustration_project,
                        intent_dict["slides"],
                        user_id=task.user_id or illustration_project.owner_id,
                        on_progress=lambda done, total: _illustration_progress(db, task, done, total),
                    )
                    if attached:
                        # JSON 列不感知原地修改，必须显式回写才会持久化。
                        artifact_version.snapshot_json = intent_dict
                        db.flush()
                        logger.info("为 %s 页生成配图（任务 %s）", len(attached), task_id)

            # Step 5: 生成课件文件
            images = resolve_slide_images(db, task.project_id, intent_dict)
            pptx_path = generate_pptx(intent_dict, rag_docs, references, images=images)
            touch_task(db, task, "pptx")

            docx_path = generate_docx(intent_dict, rag_docs, references, images=images)
            touch_task(db, task, "docx")

            html_path = generate_html(intent_dict, rag_docs, references)
            touch_task(db, task, "html")

            # Step 6: 记录输出
            outputs = _build_outputs(pptx_path, docx_path, html_path)
            touch_task(db, task, "persist")
            _persist_output_files(
                task.session_id,
                outputs,
                db,
                user_id=task.user_id,
                project_id=task.project_id,
                artifact_version_id=task.artifact_version_id,
            )
            task.status = "completed"
            task.progress = 100
            task.outputs = [o.model_dump() for o in outputs]
            task.error = None
            task.error_code = None
            task.heartbeat_at = utcnow()
            task.updated_at = task.heartbeat_at
            task.completed_at = task.heartbeat_at
            db.commit()
            logger.info("Task %s completed: %d files generated", task_id, len(outputs))

        except Exception as e:
            logger.exception("Task %s failed", task_id)
            db.rollback()
            failed_task = db.query(Task).filter(Task.task_id == task_id).first()
            if failed_task is not None:
                now = utcnow()
                failed_task.status = "failed"
                failed_task.error_code = "GENERATION_FAILED"
                failed_task.error = str(e)
                failed_task.updated_at = now
                failed_task.heartbeat_at = now
                failed_task.completed_at = now
                db.commit()
            if raise_errors:
                raise


# ── 模块级单例 ────────────────────────────────────────

_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator


# ── 内部工具函数 ──────────────────────────────────────

def _build_confirm_text(result: IntentResult) -> str:
    """用意图结果构建确认文本。"""
    lines = [
        f"课程主题：{result.teaching_goal}",
        f"授课对象：{result.target_audience}",
        f"课时：{result.duration_minutes}分钟",
        f"教学风格：{result.style_preference}",
        f"知识点：{' → '.join(kp.title for kp in result.knowledge_points)}",
        "",
        "请确认以上信息，或告诉我需要修改的地方。",
    ]
    return "\n".join(lines)


def _load_references(session_id: str, db: DBSession) -> list[ReferenceMaterial]:
    """加载该会话上传的参考资料并解析。"""
    from backend.models.file import FileRecord

    files = db.query(FileRecord).filter(FileRecord.session_id == session_id).all()
    refs = []
    for f in files:
        path = Path(f.stored_path)
        if not path.exists():
            continue
        try:
            if f.file_type == "pdf":
                text = parse_pdf(str(path))
            elif f.file_type == "word":
                text = parse_docx(str(path))
            else:
                continue
            refs.append(ReferenceMaterial(
                file_id=f.file_id,
                file_type=f.file_type,
                extracted_text=text,
                key_topics=[],
                format_notes=f.ref_description,
            ))
        except Exception:
            logger.exception("Failed to parse file %s", f.file_id)
    return refs


def _plan_stage_sink(task_id: str) -> Callable[[str], None]:
    """Forward blueprint sub-stages into the generation task.

    构建蓝图是整条链路里最慢的一步，它内部的子阶段才是"AI 现在在做什么"最具体
    的答案。这里用独立的短会话写进度，所以在蓝图还没建完时提交进度，也不会把
    调用方正在建的蓝图一起提交掉。
    """
    from backend.db.database import SessionLocal

    def forward(stage: str) -> None:
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.task_id == task_id).first()
            if task is None:
                return
            touch_task(db, task, "plan", detail=stage)
        finally:
            db.close()

    return forward


def _build_outputs(pptx_path: str, docx_path: str, html_path: str) -> list[OutputFile]:
    """构建输出文件信息列表。"""
    outputs = []
    for path_str, file_type in [(pptx_path, "pptx"), (docx_path, "docx"), (html_path, "html")]:
        p = Path(path_str)
        if p.exists():
            file_id = p.stem
            outputs.append(OutputFile(
                file_id=file_id,
                file_type=file_type,
                file_name=p.name,
                size_kb=round(p.stat().st_size / 1024, 2),
                download_url=f"/api/v1/download/{file_id}",
            ))
    return outputs


def _persist_output_files(
    session_id: str,
    outputs: list[OutputFile],
    db: DBSession,
    *,
    user_id: str | None = None,
    project_id: str | None = None,
    artifact_version_id: str | None = None,
) -> None:
    """Register generated files so task links and the download API share one path."""
    from backend.models.file import FileRecord

    for output in outputs:
        path = settings.output_dir / output.file_name
        if not path.exists():
            continue
        existing = db.query(FileRecord).filter(FileRecord.file_id == output.file_id).first()
        if existing:
            continue
        if not user_id:
            raise RuntimeError("Generated files require an authenticated owner")
        try:
            ensure_storage_capacity(db, user_id, path.stat().st_size)
        except Exception:
            remove_managed_file(path, root=settings.output_dir)
            raise
        db.add(
            FileRecord(
                file_id=output.file_id,
                user_id=user_id,
                project_id=project_id,
                artifact_version_id=artifact_version_id,
                session_id=session_id,
                original_name=output.file_name,
                file_type=output.file_type,
                stored_path=str(path),
                size_kb=output.size_kb,
                ref_description="generated",
                upload_time=datetime.now(timezone.utc),
            )
        )
