from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.reports import DashboardOut
from services import report_service
from storage.models.user import User

router = APIRouter(prefix="/v1/reports", tags=["reports"])


@router.get("/dashboard")
async def get_dashboard(
    month: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[DashboardOut]:
    result = await report_service.get_dashboard(db, current_user.id, month)
    return ok(result, "Dashboard obtenido correctamente.")
