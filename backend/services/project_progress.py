"""Aggregate every in-flight job of one project into a single progress snapshot.

The project views used to poll their own REST endpoints on a timer. One snapshot
per project lets a single stream replace all of those timers, and because the
watcher only emits when the snapshot changes, an idle project costs nothing.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from backend.models.material import Material
from backend.models.task import Task
from backend.models.versioning import ExportRecord

ACTIVE_TASK_STATUSES = ("pending", "processing")
ACTIVE_EXPORT_STATUSES = ("pending", "processing")
ACTIVE_MATERIAL_STATUSES = ("queued", "processing")


def _latest_update(db: DBSession, model: Any, project_id: str) -> str | None:
    value = (
        db.query(func.max(model.updated_at))
        .filter(model.project_id == project_id)
        .scalar()
    )
    return value.isoformat() if value is not None else None


def build_project_progress(db: DBSession, project_id: str) -> dict[str, Any]:
    """Return only the jobs that are still running, newest first.

    Finished rows are deliberately excluded. The snapshot exists to describe
    in-flight work and its fingerprint decides when a frame is sent, so an idle
    project has to produce a stable payload; a client that needs the finished
    rows keeps using the REST list endpoints.
    """
    tasks = (
        db.query(Task)
        .filter(Task.project_id == project_id, Task.status.in_(ACTIVE_TASK_STATUSES))
        .order_by(Task.created_at.desc())
        .all()
    )
    exports = (
        db.query(ExportRecord)
        .filter(
            ExportRecord.project_id == project_id,
            ExportRecord.status.in_(ACTIVE_EXPORT_STATUSES),
        )
        .order_by(ExportRecord.updated_at.desc())
        .all()
    )
    materials = (
        db.query(Material)
        .filter(
            Material.project_id == project_id,
            Material.deleted_at.is_(None),
            Material.status.in_(ACTIVE_MATERIAL_STATUSES),
        )
        .order_by(Material.updated_at.desc())
        .all()
    )

    # The active lists alone cannot report a job that starts and finishes between
    # two ticks: both snapshots would be empty and the watcher would consider
    # nothing changed. This cursor moves on any write to the project's jobs, so
    # every transition produces a frame.
    revision = max(
        (
            value
            for value in (
                _latest_update(db, Task, project_id),
                _latest_update(db, ExportRecord, project_id),
                _latest_update(db, Material, project_id),
            )
            if value is not None
        ),
        default="",
    )

    return {
        "project_id": project_id,
        "revision": revision,
        "active": bool(tasks or exports or materials),
        "tasks": [
            {
                "task_id": task.task_id,
                "task_type": task.task_type,
                "status": task.status,
                "progress": task.progress or 0,
            }
            for task in tasks
        ],
        "exports": [
            {"export_id": record.export_id, "status": record.status} for record in exports
        ],
        "materials": [
            {"material_id": material.material_id, "status": material.status}
            for material in materials
        ],
    }
