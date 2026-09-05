"""Errores de aplicación con `code` estable (inglés) y `message` para el usuario (español).

El cliente decide por `code`, nunca por `message` (CLAUDE.md). Un
`AppError` se traduce en `core/middleware.py` al envelope `ApiResponse`
con `is_success=false`.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code: int = 400
    code: str = "APP_ERROR"

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        code: str | None = None,
        **extra: Any,
    ) -> None:
        self.message = message
        self.field = field
        if code is not None:
            self.code = code
        self.extra = extra
        super().__init__(message)

    def data(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code}
        if self.field is not None:
            payload["field"] = self.field
        payload.update(self.extra)
        return payload


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class ValidationAppError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"


class RateLimitedError(AppError):
    status_code = 429
    code = "RATE_LIMITED"
