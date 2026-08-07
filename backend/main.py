"""Easy-Teach 后端入口 — FastAPI 应用"""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from core import configure_logging, ensure_runtime_directories, register_exception_handlers
from db.database import Base, engine
from routers import chat, export, generate, session, speech, upload
from schemas import HealthResponse

# 导入所有 ORM 模型，确保它们注册到 Base.metadata
import models  # noqa: F401 — 触发 models/__init__.py 中的全部注册

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_runtime_directories()
    Base.metadata.create_all(bind=engine)
    logger.info("Starting %s %s", settings.app_name, settings.app_version)
    yield
    logger.info("Stopping %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
)

register_exception_handlers(app)


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    started_at = time.perf_counter()

    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "%s %s -> %s %.1fms request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
        request_id,
    )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由注册 — 统一前缀 /api/v1
app.include_router(session.router, prefix="/api/v1/sessions", tags=["Sessions"])
app.include_router(chat.router, prefix="/api/v1/sessions", tags=["Chat"])
app.include_router(upload.router, prefix="/api/v1", tags=["Files"])
app.include_router(generate.router, prefix="/api/v1", tags=["Generation"])
app.include_router(export.router, prefix="/api/v1", tags=["Export"])
app.include_router(speech.router, prefix="/api/v1/speech", tags=["Speech"])


@app.get("/", response_model=HealthResponse)
def root():
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "status": "running",
    }


@app.get("/health", response_model=HealthResponse)
def health():
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "status": "ok",
    }
