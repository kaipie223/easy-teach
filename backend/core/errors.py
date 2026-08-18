from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "bad_request",
        status_code: int = 400,
        details: Any | None = None,
        recoverable: bool = True,
        suggested_action: str | None = None,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details
        self.recoverable = recoverable
        self.suggested_action = suggested_action


def get_request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        return request_id
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    return request_id


def build_error_response(
    request: Request,
    *,
    code: str,
    message: str,
    details: Any | None = None,
    recoverable: bool = True,
    suggested_action: str | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": get_request_id(request),
            "recoverable": recoverable,
            "suggested_action": suggested_action,
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status_code,
            content=build_error_response(
                request,
                code=exc.code,
                message=exc.message,
                details=exc.details,
                recoverable=exc.recoverable,
                suggested_action=exc.suggested_action,
            ),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=build_error_response(
                request,
                code="http_error",
                message=str(exc.detail),
                recoverable=exc.status_code < 500,
            ),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=build_error_response(
                request,
                code="validation_error",
                message="Request validation failed.",
                details=exc.errors(),
                recoverable=True,
                suggested_action="请修正请求字段后重试",
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        logger.exception("Unhandled error while processing request")
        return JSONResponse(
            status_code=500,
            content=build_error_response(
                request,
                code="internal_error",
                message="Internal server error.",
                recoverable=True,
                suggested_action="请稍后重试；如果问题持续，请联系管理员",
            ),
        )
