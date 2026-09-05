from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.response import ApiResponse, ok
from dependencies import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> ApiResponse[dict[str, Any]]:
    return ok({"status": "ok"})


@router.get("/health/ready")
async def health_ready(db: AsyncSession = Depends(get_db)) -> ApiResponse[dict[str, Any]]:
    await db.execute(text("SELECT 1"))
    return ok({"status": "ready"})
