"""Envelope de respuesta uniforme: `ApiResponse{is_success, message, data}`."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ApiResponse[T](BaseModel):
    is_success: bool
    message: str
    data: T | None = None


def ok[T](data: T | None = None, message: str = "Ok") -> ApiResponse[T]:
    return ApiResponse[T](is_success=True, message=message, data=data)


def fail(message: str, data: dict[str, Any] | None = None) -> ApiResponse[dict[str, Any]]:
    return ApiResponse[dict[str, Any]](is_success=False, message=message, data=data)
