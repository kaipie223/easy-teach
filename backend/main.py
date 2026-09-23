"""Easy-Teach 后端入口 — FastAPI 应用"""

import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import settings
from backend.core import configure_logging, ensure_runtime_directories, register_exception_handlers
from backend.core.errors import build_error_response
from backend.db.database import engine
from backend.db.schema_revision import log_schema_status, schema_check
from backend.routers import (
    admin,
    auth,
    brief,
    chat,
    courseware,
    export,
    generate,
    knowledge,
    materials,
    project_chat,
    projects,
    revisions,
    session,
    speech,
    upload,
)
from backend.schemas import HealthResponse
from backend.services.limits import check_request_rate_limit

# 导入所有 ORM 模型，确保它们注册到 Base.metadata
import backend.models  # noqa: F401 — 触发 models/__init__.py 中的全部注册

configure_logging()
if not settings.chroma_anonymized_telemetry:
    # Chroma 0.5 still invokes a disabled PostHog client whose newer API logs
    # a false capture signature error. Telemetry remains disabled by settings.
    logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)
logger = logging.getLogger(__name__)
IS_PRODUCTION = settings.environment.lower() in {"production", "prod"}
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_runtime_directories()
    logger.info("Starting %s %s", settings.app_name, settings.app_version)
    log_schema_status()
    yield
    logger.info("Stopping %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)

register_exception_handlers(app)


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    supplied_request_id = request.headers.get("X-Request-ID", "")
    request_id = (
        supplied_request_id
        if REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
        else str(uuid.uuid4())
    )
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


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=()")
    if request.url.path.startswith("/api/v1/auth/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.middleware("http")
async def enforce_request_rate_limit(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in {"/", "/health"}:
        return await call_next(request)
    result = await run_in_threadpool(check_request_rate_limit, request)
    if result.exceeded:
        return JSONResponse(
            status_code=429,
            content=build_error_response(
                request,
                code="REQUEST_RATE_LIMITED",
                message="请求过于频繁",
                details={"requests_per_minute": result.limit},
                suggested_action=f"请等待 {result.retry_after} 秒后重试",
            ),
            headers={"Retry-After": str(result.retry_after)},
        )
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由注册 — 统一前缀 /api/v1
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(projects.router, prefix="/api/v1/projects", tags=["Projects"])
app.include_router(brief.router, prefix="/api/v1/projects", tags=["TeachingBrief"])
app.include_router(courseware.router, prefix="/api/v1/projects", tags=["Courseware"])
app.include_router(revisions.project_router, prefix="/api/v1/projects", tags=["Revisions"])
app.include_router(revisions.export_router, prefix="/api/v1/exports", tags=["Exports"])
app.include_router(project_chat.router, prefix="/api/v1/projects", tags=["ProjectChat"])
app.include_router(session.router, prefix="/api/v1/sessions", tags=["Sessions"])
app.include_router(chat.router, prefix="/api/v1/sessions", tags=["Chat"])
app.include_router(upload.router, prefix="/api/v1", tags=["Files"])
app.include_router(materials.router, prefix="/api/v1", tags=["Materials"])
app.include_router(knowledge.router, prefix="/api/v1/knowledge", tags=["Knowledge"])
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
    checks = _dependency_checks()
    healthy = all(
        item["status"] in {"ok", "not_configured", "not_indexed"}
        for item in checks.values()
    )
    visible_checks = checks
    if settings.environment.lower() in {"production", "prod"}:
        # Public health checks must be useful to a load balancer without
        # disclosing filesystem paths, dependency hosts or exception details.
        visible_checks = {
            name: {"status": item["status"]}
            for name, item in checks.items()
        }
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "status": "ok" if healthy else "degraded",
        "checks": visible_checks,
    }


def _dependency_checks() -> dict[str, dict[str, str]]:
    """Return dependency status, including persisted Chroma index readability."""
    checks: dict[str, dict[str, str]] = {}

    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        checks["database"] = {"status": "ok", "engine": engine.dialect.name}
    except Exception as exc:
        logger.warning("Database health check failed: %s", exc)
        checks["database"] = {"status": "error", "message": str(exc)}

    chroma_path = settings.chroma_persist_dir
    if not chroma_path.exists():
        checks["chroma"] = {"status": "missing", "path": str(chroma_path)}
    else:
        try:
            import chromadb

            client = chromadb.PersistentClient(path=str(chroma_path))
            collections = client.list_collections()
            names = [item.name if hasattr(item, "name") else str(item) for item in collections]
            knowledge_names = [name for name in names if name.startswith("knowledge_user_")]
            if not knowledge_names:
                checks["chroma"] = {"status": "not_indexed", "path": str(chroma_path), "count": 0}
            else:
                count = 0
                for collection_name in knowledge_names:
                    collection = client.get_collection(collection_name)
                    collection_count = collection.count()
                    count += collection_count
                    # count() only reads Chroma metadata. peek() also opens the
                    # persisted HNSW index and catches incomplete/corrupt files.
                    if collection_count > 0:
                        preview = collection.peek(limit=1)
                        if not preview.get("ids"):
                            raise RuntimeError(
                                "Chroma collection contains records but cannot read an index entry"
                            )
                checks["chroma"] = {
                    "status": "ok" if count > 0 else "not_indexed",
                    "path": str(chroma_path),
                    "count": count,
                    "collections": len(knowledge_names),
                }
        except Exception as exc:
            logger.warning("Chroma health check failed: %s", exc)
            checks["chroma"] = {"status": "error", "message": str(exc)}

    checks["database_schema"] = schema_check()
    checks["redis"] = _redis_health_check()
    return checks


def _redis_health_check() -> dict[str, str]:
    from urllib.parse import urlparse
    import socket

    parsed = urlparse(settings.redis_url)
    if parsed.scheme in {"", "memory", "disabled"}:
        return {"status": "not_configured"}

    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return {"status": "ok", "host": host, "port": str(port)}
    except OSError as exc:
        return {"status": "error", "message": f"{host}:{port} unavailable ({exc})"}
