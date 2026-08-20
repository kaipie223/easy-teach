from .chat import router as chat_router
from .export import router as export_router
from .generate import router as generate_router
from .session import router as session_router
from .speech import router as speech_router
from .upload import router as upload_router
from .courseware import router as courseware_router

__all__ = [
    "chat_router",
    "export_router",
    "generate_router",
    "session_router",
    "speech_router",
    "upload_router",
    "courseware_router",
]
