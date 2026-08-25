from backend.db.database import Base
from .session import Session, ChatMessage, gen_id
from .file import FileRecord
from .task import Task
from .user import User
from .project import Project
from .brief import TeachingBrief
from .material import EvidenceChunk, Material, MaterialAnalysis, MaterialBinding
from .knowledge import KnowledgeDocument
from .courseware import CoursewarePlan
from .versioning import ArtifactVersion, ExportRecord, RevisionPatch

__all__ = [
    "Base",
    "Session",
    "ChatMessage",
    "gen_id",
    "FileRecord",
    "Task",
    "User",
    "Project",
    "TeachingBrief",
    "Material",
    "MaterialAnalysis",
    "MaterialBinding",
    "EvidenceChunk",
    "KnowledgeDocument",
    "CoursewarePlan",
    "ArtifactVersion",
    "RevisionPatch",
    "ExportRecord",
]
