"""Serialize project-scoped version allocation across threads and database workers."""

from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
from typing import Iterator

from sqlalchemy.orm import Session as DBSession

from backend.models.project import Project


_LOCAL_LOCKS = tuple(RLock() for _ in range(256))


@contextmanager
def project_version_lock(db: DBSession, project_id: str) -> Iterator[None]:
    """Lock one project before reading and incrementing any project version."""
    lock = _LOCAL_LOCKS[hash(project_id) % len(_LOCAL_LOCKS)]
    with lock:
        (
            db.query(Project)
            .filter(Project.project_id == project_id)
            .with_for_update()
            .one()
        )
        yield
