"""核心编排器 — 串联意图理解 → RAG → 资料解析 → 课件生成的全流程。

这是整个系统的核心调度模块，被 routers/ 调用。
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

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
from backend.services.intent import get_intent_analyzer
from backend.services.brief import (
    brief_to_intent,
    get_latest_brief,
    normalize_content,
    persist_intent_result,
    to_info,
)
from backend.services.courseware import build_courseware_plan
from backend.services.rag import search as rag_search
from backend.services.quality import require_courseware_quality
from backend.services.task_queue import claim_task_attempt, task_info_values, touch_task, utcnow
from backend.services.versions import ensure_initial_version
from backend.services.parser import parse_docx, parse_pdf
from backend.services.generator import generate_docx, generate_html, generate_pptx
from backend.services.limits import ensure_storage_capacity, ensure_task_capacity
from backend.services.uploads import remove_managed_file

logger = logging.getLogger(__name__)


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
    ) -> AsyncGenerator[ChatEvent, None]:
        """处理一轮对话，SSE 流式返回事件。"""
        messages = history or [{"role": "user", "content": message}]

        # 1. 先发送确认收到
        yield ChatEvent(event_type=MessageType.TEXT, content="收到您的消息，正在分析教学意图……")

        # 2. 调用意图分析
        result = self.intent_analyzer.analyze(session_id, messages)
        brief = None
        if db is not None and session is not None:
            brief, result = persist_intent_result(db, session, result)
        brief_content = normalize_content(brief.content_json) if brief is not None else None
        brief_payload = to_info(brief).model_dump(mode="json") if brief is not None else None

        # 3. 流式输出确认/追问
        if not result.is_complete:
            # 信息不全 → 追问
            yield ChatEvent(
                event_type=MessageType.QUESTION,
                content=result.follow_up_question or "请补充更多信息",
                data={
                    "prompt": result.follow_up_question or "请补充更多信息",
                    "missing_info": result.missing_info,
                    "options": [],
                    "allow_free": True,
                    "brief": brief_payload,
                },
            )
        else:
            # 信息完整 → 确认总结
            yield ChatEvent(
                event_type=MessageType.CONFIRM,
                content=result.confirm_summary or _build_confirm_text(result),
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
            touch_task(db, task, 10)

            # Step 1: 锁定已确认 brief；匿名 M0 会话继续使用旧意图缓存。
            confirmed_brief = (
                get_latest_brief(db, project_id=task.project_id)
                if task.project_id
                else None
            )
            intent = (
                brief_to_intent(confirmed_brief)
                if confirmed_brief is not None and confirmed_brief.status == "confirmed"
                else self.intent_analyzer.lock_intent(task.session_id)
            )
            touch_task(db, task, 20)

            # Step 2: RAG 检索
            query = f"{intent.teaching_goal} {' '.join(kp.title for kp in intent.knowledge_points)}"
            rag_docs = _sync(rag_search(query, top_k=5, owner_id=task.user_id))
            touch_task(db, task, 40)

            # Step 3: 解析参考资料
            references = _load_references(task.session_id, db)
            touch_task(db, task, 60)

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
                    plan = build_courseware_plan(db, project, rag_docs=rag_docs)
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
                quality_report = require_courseware_quality(intent_dict)
                if artifact_version is not None:
                    artifact_version.quality_status = quality_report["status"]
                    artifact_version.quality_report = quality_report
                    db.flush()
            touch_task(db, task, 70)

            # Step 5: 生成课件文件
            pptx_path = _sync(generate_pptx(intent_dict, rag_docs, references))
            touch_task(db, task, 80)

            docx_path = _sync(generate_docx(intent_dict, rag_docs, references))
            touch_task(db, task, 90)

            html_path = _sync(generate_html(intent_dict, rag_docs, references))

            # Step 6: 记录输出
            outputs = _build_outputs(pptx_path, docx_path, html_path)
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


def _sync(coro):
    """在同步上下文中运行异步函数。"""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()


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
                text = _sync(parse_pdf(str(path)))
            elif f.file_type == "word":
                text = _sync(parse_docx(str(path)))
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
