"""Authorization lookups shared by session-oriented M0 routes and M1 resources."""

from sqlalchemy.orm import Session as DBSession

from backend.core.errors import ApiError
from backend.models.file import FileRecord
from backend.models.material import Material
from backend.models.project import Project
from backend.models.session import Session
from backend.models.task import Task
from backend.models.user import User


def get_project_for_user(
    db: DBSession,
    project_id: str,
    user: User,
    *,
    include_deleted: bool = False,
) -> Project:
    query = db.query(Project).filter(Project.project_id == project_id)
    if user.role != "admin":
        query = query.filter(Project.owner_id == user.user_id)
    if not include_deleted:
        query = query.filter(Project.deleted_at.is_(None))
    project = query.first()
    if project is None:
        raise ApiError("项目不存在", code="PROJECT_NOT_FOUND", status_code=404)
    return project


def get_session_for_user(
    db: DBSession,
    session_id: str,
    user: User | None,
) -> Session:
    query = db.query(Session).filter(Session.session_id == session_id)
    if user is None:
        query = query.filter(Session.user_id.is_(None))
    elif user.role != "admin":
        query = query.filter(Session.user_id == user.user_id)
    session = query.first()
    if session is None:
        raise ApiError("会话不存在", code="SESSION_NOT_FOUND", status_code=404)
    return session


def get_file_for_user(db: DBSession, file_id: str, user: User | None) -> FileRecord:
    query = db.query(FileRecord).filter(FileRecord.file_id == file_id)
    if user is None:
        query = query.filter(FileRecord.user_id.is_(None))
    elif user.role != "admin":
        query = query.filter(FileRecord.user_id == user.user_id)
    record = query.first()
    if record is None:
        raise ApiError("文件不存在", code="FILE_NOT_FOUND", status_code=404)
    return record


def get_material_for_user(db: DBSession, material_id: str, user: User) -> Material:
    query = db.query(Material).filter(
        Material.material_id == material_id,
        Material.deleted_at.is_(None),
    )
    if user.role != "admin":
        query = query.filter(Material.owner_id == user.user_id)
    material = query.first()
    if material is None:
        raise ApiError("资料不存在", code="MATERIAL_NOT_FOUND", status_code=404)
    return material


def get_task_for_user(db: DBSession, task_id: str, user: User | None) -> Task:
    query = db.query(Task).filter(Task.task_id == task_id)
    if user is None:
        query = query.filter(Task.user_id.is_(None))
    elif user.role != "admin":
        query = query.filter(Task.user_id == user.user_id)
    task = query.first()
    if task is None:
        raise ApiError("任务不存在", code="TASK_NOT_FOUND", status_code=404)
    return task
