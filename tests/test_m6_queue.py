"""M6 durable queue lifecycle and deterministic quality checks."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.database import Base
from backend.models.material import Material, MaterialAnalysis
from backend.models.session import Session
from backend.models.task import Task
from backend.services.orchestrator import Orchestrator
from backend.services.quality import inspect_courseware
from backend.services.task_queue import (
    claim_task_attempt,
    enqueue_generation,
    enqueue_material_analysis,
    prepare_task_retry,
    recover_stale_jobs,
    touch_task,
)


def local_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)()


def new_task(db, *, task_id="task_m6", status="pending", retry_count=0, max_retries=2):
    db.add(
        Task(
            task_id=task_id,
            user_id="user_m6",
            session_id="session_m6",
            status=status,
            retry_count=retry_count,
            max_retries=max_retries,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return db.query(Task).filter(Task.task_id == task_id).one()


def test_task_claim_updates_heartbeat_and_progress():
    db = local_db()
    try:
        task = new_task(db)
        assert claim_task_attempt(db, task.task_id) is True
        task = db.query(Task).filter(Task.task_id == task.task_id).one()
        assert task.status == "processing"
        assert task.started_at is not None
        assert task.heartbeat_at is not None

        touch_task(db, task, 45)
        assert task.progress == 45
        assert task.updated_at is not None
    finally:
        db.close()


def test_retry_moves_task_back_to_pending_then_stops(monkeypatch):
    db = local_db()
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=db.get_bind())
    monkeypatch.setattr("backend.services.task_queue.SessionLocal", session_factory)
    try:
        task = new_task(db, status="failed", max_retries=1)
        assert prepare_task_retry(task.task_id, "temporary failure") is True
        db.expire_all()
        task = db.query(Task).filter(Task.task_id == task.task_id).one()
        assert task.status == "pending"
        assert task.retry_count == 1

        assert prepare_task_retry(task.task_id, "final failure") is False
        db.expire_all()
        task = db.query(Task).filter(Task.task_id == task.task_id).one()
        assert task.status == "failed"
        assert task.error_code == "TASK_FAILED"
    finally:
        db.close()


def test_generation_idempotency_returns_existing_task():
    db = local_db()
    try:
        session = Session(
            session_id="session_m6",
            user_id="user_m6",
            subject="测试",
            created_at=datetime.now(timezone.utc),
        )
        db.add(session)
        db.commit()
        orchestrator = Orchestrator()
        first = orchestrator.create_generation_task(
            session,
            db,
            idempotency_key="request-m6-001",
        )
        second = orchestrator.create_generation_task(
            session,
            db,
            idempotency_key="request-m6-001",
        )
        assert first.task_id == second.task_id
        assert db.query(Task).count() == 1
    finally:
        db.close()


def test_enqueue_records_celery_id_without_replacing_request_session(monkeypatch):
    db = local_db()
    try:
        task = new_task(db)
        monkeypatch.setattr(
            "backend.services.task_queue._dispatch",
            lambda name, entity_id: type("Result", (), {"id": "celery-m6-001"})(),
        )
        assert enqueue_generation(task.task_id, db=db) == "celery-m6-001"
        assert task.celery_task_id == "celery-m6-001"
    finally:
        db.close()


def test_stale_pending_task_is_requeued(monkeypatch):
    db = local_db()
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=db.get_bind())
    monkeypatch.setattr("backend.services.task_queue.SessionLocal", session_factory)
    dispatched = []
    monkeypatch.setattr(
        "backend.services.task_queue.enqueue_generation",
        lambda task_id, force=False: dispatched.append((task_id, force)) or task_id,
    )
    try:
        task = new_task(db, task_id="task_stale_pending")
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        task.created_at = old
        task.updated_at = old
        task.celery_task_id = "lost-worker-task"
        db.commit()

        result = recover_stale_jobs()

        db.expire_all()
        task = db.query(Task).filter(Task.task_id == task.task_id).one()
        assert result["recovered"] == 1
        assert task.status == "pending"
        assert task.retry_count == 1
        assert task.celery_task_id is None
        assert dispatched == [("task_stale_pending", True)]
    finally:
        db.close()


def test_enqueue_material_analysis_dispatches_video_parser(monkeypatch, tmp_path):
    db = local_db()
    try:
        material = Material(
            material_id="mat_video_queue",
            owner_id="user_m6",
            project_id=None,
            original_name="lesson.mp4",
            file_type="video",
            mime_type="video/mp4",
            stored_path=str(tmp_path / "lesson.mp4"),
            size_bytes=24,
            checksum_sha256="a" * 64,
            status="uploaded",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        analysis = MaterialAnalysis(
            analysis_id="analysis_video_queue",
            material_id=material.material_id,
            run_number=1,
            parser_name="video-parser-model",
            parser_version="0.3.0",
            status="pending",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add_all([material, analysis])
        db.commit()
        dispatched = []
        monkeypatch.setattr(
            "backend.services.task_queue._dispatch",
            lambda name, entity_id: dispatched.append((name, entity_id))
            or type("Result", (), {"id": "analysis_video_queue"})(),
        )

        assert enqueue_material_analysis(analysis.analysis_id, db=db) == analysis.analysis_id
        db.expire_all()
        material = db.query(Material).filter(Material.material_id == material.material_id).one()
        analysis = db.query(MaterialAnalysis).filter(MaterialAnalysis.analysis_id == analysis.analysis_id).one()
        assert dispatched == [("easy_teach.parse_material", "analysis_video_queue")]
        assert material.status == "queued"
        assert analysis.status == "pending"
    finally:
        db.close()


def test_quality_report_distinguishes_blocking_errors_and_warnings():
    invalid = inspect_courseware(
        {
            "title": "课程",
            "target_audience": "教师",
            "duration_minutes": 10,
            "teaching_goal": "目标",
            "slides": [],
            "lesson_sections": [],
            "interactions": [],
        }
    )
    assert invalid["status"] == "failed"
    assert invalid["errors"][0]["code"] == "NO_SLIDES"

    warning = inspect_courseware(
        {
            "title": "课程",
            "target_audience": "教师",
            "duration_minutes": 10,
            "teaching_goal": "目标",
            "slides": [
                {
                    "slide_id": "slide_1",
                    "order": 1,
                    "title": "导入",
                    "purpose": "导入",
                    "bullets": ["内容"],
                }
            ],
            "lesson_sections": [],
            "interactions": [],
        }
    )
    assert warning["status"] == "warning"
    assert any(item["code"] == "NO_EVIDENCE_REFS" for item in warning["warnings"])
