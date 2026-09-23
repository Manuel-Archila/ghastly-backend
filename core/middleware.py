"""Middleware de `request_id` y manejadores de excepción -> `ApiResponse`."""

from __future__ import annotations

import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from core.errors import AppError
from core.response import fail

REQUEST_ID_HEADER = "X-Request-Id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Genera (o propaga) un `request_id` por request y lo liga al logger.

    Nunca se loguean montos ni descripciones (CLAUDE.md); el `request_id`
    es justamente lo que sí es seguro loguear y correlacionar.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def register_exception_handlers(app: FastAPI) -> None:
    logger = structlog.get_logger("ghastly.errors")

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        envelope = fail(exc.message, exc.data())
        return JSONResponse(status_code=exc.status_code, content=envelope.model_dump())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Un `ValueError` lanzado dentro de un validador viaja en
        # `ctx.error` y no es serializable: sin esto la respuesta 422 se
        # convertía en un 500.
        errors = jsonable_encoder(exc.errors(), custom_encoder={ValueError: str})
        envelope = fail(
            "Los datos enviados no son válidos.",
            {"code": "VALIDATION_ERROR", "errors": errors},
        )
        return JSONResponse(status_code=422, content=envelope.model_dump())

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.error("unhandled_exception", request_id=request_id, error_type=type(exc).__name__)
        envelope = fail("Ocurrió un error inesperado.", {"code": "INTERNAL_ERROR"})
        return JSONResponse(status_code=500, content=envelope.model_dump())
