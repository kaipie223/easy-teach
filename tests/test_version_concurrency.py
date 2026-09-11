from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from backend.db.database import Base
from backend.models.brief import TeachingBrief
from backend.models.project import Project
from backend.models.user import User
from backend.services.version_allocator import project_version_lock


def test_project_version_allocator_serializes_concurrent_writers(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'versions.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = factory()
    try:
        db.add(
            User(
                user_id="u_concurrent",
                email="concurrent@example.com",
                display_name="Concurrent Teacher",
                password_hash="not-used",
                role="teacher",
                is_active=True,
            )
        )
        db.add(
            Project(
                project_id="p_concurrent",
                owner_id="u_concurrent",
                title="Concurrent Versions",
            )
        )
        db.commit()
    finally:
        db.close()

    barrier = Barrier(4)

    def create_brief(index: int) -> int:
        worker_db = factory()
        try:
            barrier.wait()
            with project_version_lock(worker_db, "p_concurrent"):
                version = (
                    worker_db.query(func.max(TeachingBrief.version))
                    .filter(TeachingBrief.project_id == "p_concurrent")
                    .scalar()
                    or 0
                ) + 1
                worker_db.add(
                    TeachingBrief(
                        brief_id=f"brief_{index}",
                        user_id="u_concurrent",
                        project_id="p_concurrent",
                        version=version,
                        status="draft",
                        content_json={},
                        source_refs={},
                        confidence={},
                        created_at=datetime.now(timezone.utc),
                    )
                )
                worker_db.commit()
                return version
        finally:
            worker_db.close()

    with ThreadPoolExecutor(max_workers=4) as executor:
        versions = sorted(executor.map(create_brief, range(4)))

    assert versions == [1, 2, 3, 4]

    db = factory()
    try:
        stored = (
            db.query(TeachingBrief.version)
            .filter(TeachingBrief.project_id == "p_concurrent")
            .order_by(TeachingBrief.version)
            .all()
        )
        assert [row.version for row in stored] == [1, 2, 3, 4]
    finally:
        db.close()
