from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.meta import InfoOut
from storage.models.user import User

router = APIRouter(tags=["health"])


def _app_version() -> str:
    try:
        return version("ghastly-backend")
    except PackageNotFoundError:
        return "0.0.0-dev"


@router.get("/health")
async def health() -> ApiResponse[dict[str, Any]]:
    return ok({"status": "ok"})


@router.get("/health/ready")
async def health_ready(db: AsyncSession = Depends(get_db)) -> ApiResponse[dict[str, Any]]:
    await db.execute(text("SELECT 1"))
    return ok({"status": "ready"})


@router.get("/info")
async def info(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[InfoOut]:
    result = await db.execute(
        text("SELECT version, name FROM schema_version ORDER BY installed_rank DESC LIMIT 1")
    )
    row = result.first()
    return ok(
        InfoOut(
            version=_app_version(),
            migration_version=row.version if row else None,
            migration_name=row.name if row else None,
        )
    )
