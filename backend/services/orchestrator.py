"""核心编排器 — 串联意图理解 → RAG → 资料解析 → 课件生成的全流程。

这是整个系统的核心调度模块，被 routers/ 调用。
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy.orm import Session as DBSession

from config import settings
from models.task import Task
from schemas import (
    ChatEvent,
    GenerationInstruction,
    IntentResult,
    KnowledgePoint,
    MessageType,
    OutputFile,
    RAGDocument,
    ReferenceMaterial,
    TaskInfo,
    TaskStatus,
)
from services.intent import get_intent_analyzer
from services.rag import search as rag_search
from services.parser import parse_docx, parse_image, parse_pdf, parse_video
from services.generator import generate_docx, generate_html, generate_pptx

logger = logging.getLogger(__name__)


class Orchestrator:
    """编排器单例。"""

    def __init__(self):
        self.intent_analyzer = get_intent_analyzer()

    # ── 对话 ──────────────────────────────────────────

    async def chat(self, session_id: str, message: str) -> AsyncGenerator[ChatEvent, None]:
        """处理一轮对话，SSE 流式返回事件。"""
        messages = [{"role": "user", "content": message}]

        # 1. 先发送确认收到
        yield ChatEvent(event_type=MessageType.TEXT, content=f"收到您的消息，正在分析教学意图……")

        # 2. 调用意图分析
        result = self.intent_analyzer.analyze(session_id, messages)

        # 3. 流式输出确认/追问
        if not result.is_complete:
            # 信息不全 → 追问
            yield ChatEvent(
                event_type=MessageType.QUESTION,
                content=result.follow_up_question or "请补充更多信息",
                data={"missing_info": result.missing_info, "options": None, "allow_free": True},
            )
        else:
            # 信息完整 → 确认总结
            yield ChatEvent(
                event_type=MessageType.CONFIRM,
                content=result.confirm_summary or _build_confirm_text(result),
                data=None,
            )

    # ── 课件生成 ──────────────────────────────────────

    def create_generation_task(self, session_id: str, db: DBSession) -> TaskInfo:
        """创建异步生成任务，写入数据库。"""
        from models.session import gen_id

        task_id = gen_id("task")
        task = Task(
            task_id=task_id,
            session_id=session_id,
            status="pending",
            progress=0,
            created_at=datetime.now(timezone.utc),
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return TaskInfo(
            task_id=task.task_id,
            session_id=task.session_id,
            status=TaskStatus(task.status),
            progress=task.progress,
        )

    def run_generation(self, task_id: str) -> None:
        """后台执行课件生成全流程 — 使用独立数据库会话，杜绝跨线程会话泄漏。"""
        from db.database import SessionLocal

        db = SessionLocal()
        try:
            self._run_generation_impl(task_id, db)
        except Exception:
            logger.exception("Task %s failed", task_id)
        finally:
            db.close()

    def _run_generation_impl(self, task_id: str, db) -> None:
        """课件生成的内部实现 — 接收独立会话，不受请求生命周期影响。"""
        task = db.query(Task).filter(Task.task_id == task_id).first()
        if not task:
            return

        try:
            task.status = "processing"
            task.progress = 10
            db.commit()

            # Step 1: 锁定意图
            intent = self.intent_analyzer.lock_intent(task.session_id)
            task.progress = 20
            db.commit()

            # Step 2: RAG 检索
            query = f"{intent.teaching_goal} {' '.join(kp.title for kp in intent.knowledge_points)}"
            rag_docs = _sync(rag_search(query, top_k=5))
            task.progress = 40
            db.commit()

            # Step 3: 解析参考资料
            references = _load_references(task.session_id, db)
            task.progress = 60
            db.commit()

            # Step 4: 知识融合
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
            task.progress = 70
            db.commit()

            # Step 5: 生成课件文件
            pptx_path = _sync(generate_pptx(intent_dict, rag_docs, references))
            task.progress = 80
            db.commit()

            docx_path = _sync(generate_docx(intent_dict, rag_docs, references))
            task.progress = 90
            db.commit()

            html_path = _sync(generate_html(intent_dict, rag_docs, references))

            # Step 6: 记录输出
            outputs = _build_outputs(pptx_path, docx_path, html_path)
            task.status = "completed"
            task.progress = 100
            task.outputs = [o.model_dump() for o in outputs]
            db.commit()
            logger.info("Task %s completed: %d files generated", task_id, len(outputs))

        except Exception as e:
            logger.exception("Task %s failed", task_id)
            task.status = "failed"
            task.error = str(e)
            db.commit()


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
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()


def _load_references(session_id: str, db: DBSession) -> list[ReferenceMaterial]:
    """加载该会话上传的参考资料并解析。"""
    from models.file import FileRecord

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
            elif f.file_type == "image":
                text = _sync(parse_image(str(path)))
            elif f.file_type == "video":
                text = _sync(parse_video(str(path)))
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
