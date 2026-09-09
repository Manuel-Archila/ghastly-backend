from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.reports import (
    AnomaliesOut,
    CashflowSeriesOut,
    CategoryBreakdownOut,
    ComparisonOut,
    DashboardOut,
    ExpectedIncomeOut,
    NetWorthHistoryOut,
    SavingsRateOut,
    TrendsOut,
    UpcomingCalendarOut,
)
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


@router.get("/by-category")
async def get_by_category(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    kind: Literal["expense", "income"] = "expense",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[CategoryBreakdownOut]:
    result = await report_service.get_category_breakdown(db, current_user.id, from_, to, kind)
    return ok(result, "Desglose por categoría obtenido correctamente.")


@router.get("/cashflow")
async def get_cashflow(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    granularity: Literal["month", "week"] = "month",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[CashflowSeriesOut]:
    result = await report_service.get_cashflow_series(db, current_user.id, from_, to, granularity)
    return ok(result, "Flujo de caja obtenido correctamente.")


@router.get("/expected-income")
async def get_expected_income(
    month: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ExpectedIncomeOut]:
    result = await report_service.get_expected_income(db, current_user.id, month)
    return ok(result, "Ingreso esperado obtenido correctamente.")


@router.get("/net-worth")
async def get_net_worth(
    months: int = Query(default=12, ge=1, le=60),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[NetWorthHistoryOut]:
    result = await report_service.get_net_worth_history(db, current_user.id, months)
    return ok(result, "Historial de patrimonio neto obtenido correctamente.")


@router.get("/trends")
async def get_trends(
    months: int = Query(default=6, ge=1, le=60),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[TrendsOut]:
    result = await report_service.get_trends(db, current_user.id, months)
    return ok(result, "Tendencia obtenida correctamente.")


@router.get("/comparison")
async def get_comparison(
    a: str,
    b: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ComparisonOut]:
    result = await report_service.get_comparison(db, current_user.id, a, b)
    return ok(result, "Comparación obtenida correctamente.")


@router.get("/anomalies")
async def get_anomalies(
    month: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[AnomaliesOut]:
    result = await report_service.get_anomalies(db, current_user.id, month)
    return ok(result, "Anomalías obtenidas correctamente.")


@router.get("/savings-rate")
async def get_savings_rate(
    months: int = Query(default=12, ge=1, le=60),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[SavingsRateOut]:
    result = await report_service.get_savings_rate(db, current_user.id, months)
    return ok(result, "Tasa de ahorro obtenida correctamente.")


@router.get("/upcoming")
async def get_upcoming(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[UpcomingCalendarOut]:
    result = await report_service.get_upcoming(db, current_user.id, days)
    return ok(result, "Próximos vencimientos obtenidos correctamente.")
