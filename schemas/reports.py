from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from schemas.budgets import BudgetCurrentOut


class NetWorthOut(BaseModel):
    net_worth_cents: int


class CashflowOut(BaseModel):
    income_cents: int
    expense_cents: int
    net_cents: int


class TopCategoryOut(BaseModel):
    category_id: UUID
    category_name: str
    net_spent_cents: int


class UpcomingItemOut(BaseModel):
    source_type: Literal[
        "installment", "recurring", "card_statement", "card_payment", "debt_payment"
    ]
    source_id: UUID
    name: str
    due_date: date
    amount_cents: int


class MonthAmountOut(BaseModel):
    month: str
    amount_cents: int


class InstallmentLiabilityOut(BaseModel):
    total_pending_cents: int
    by_month: list[MonthAmountOut]


class CategoryBreakdownItemOut(BaseModel):
    category_id: UUID
    category_name: str
    amount_cents: int
    percent_of_total: int


class CategoryBreakdownOut(BaseModel):
    kind: Literal["expense", "income"]
    from_date: date
    to_date: date
    total_cents: int
    items: list[CategoryBreakdownItemOut]


class CashflowPeriodOut(BaseModel):
    period_start: date
    income_cents: int
    expense_cents: int
    net_cents: int


class CashflowSeriesOut(BaseModel):
    granularity: Literal["month", "week"]
    from_date: date
    to_date: date
    periods: list[CashflowPeriodOut]


class ExpectedIncomeOut(BaseModel):
    month: str
    # Las dos bases calculables sin que el usuario dé un monto fijo (caso de
    # negocio 7). La base "fixed" la decide el usuario al crear/editar el
    # presupuesto — este reporte no la propone.
    previous_month_cents: int
    avg_3m_cents: int


class NetWorthHistoryOut(BaseModel):
    months: int
    points: list[MonthAmountOut]


class TrendsOut(BaseModel):
    months: int
    periods: list[CashflowPeriodOut]
    # Caso de negocio 12 (aguinaldo/bono 14): promedio con y sin ingresos
    # marcados is_extraordinary, sobre la misma ventana de `months`.
    avg_income_with_extraordinary_cents: int
    avg_income_recurring_cents: int


class CategoryComparisonItemOut(BaseModel):
    category_id: UUID
    category_name: str
    a_amount_cents: int
    b_amount_cents: int
    delta_cents: int
    percent_change: int | None


class ComparisonOut(BaseModel):
    a_month: str
    b_month: str
    a_income_cents: int
    a_expense_cents: int
    b_income_cents: int
    b_expense_cents: int
    income_change_percent: int | None
    expense_change_percent: int | None
    categories: list[CategoryComparisonItemOut]


class CategoryAnomalyOut(BaseModel):
    category_id: UUID
    category_name: str
    current_cents: int
    average_cents: int
    percent_increase: int


class AnomaliesOut(BaseModel):
    month: str
    items: list[CategoryAnomalyOut]


class SavingsRatePointOut(BaseModel):
    month: str
    income_cents: int
    expense_cents: int
    # None cuando no hubo ingreso ese mes — la tasa no está definida.
    savings_rate_percent: int | None


class SavingsRateOut(BaseModel):
    months: int
    points: list[SavingsRatePointOut]


class UpcomingCalendarOut(BaseModel):
    days: int
    items: list[UpcomingItemOut]


class DashboardOut(BaseModel):
    month: str
    net_worth: NetWorthOut
    cashflow: CashflowOut
    top_categories: list[TopCategoryOut]
    # None si el usuario no tiene un presupuesto activo — el dashboard no
    # depende de que exista uno (budget_service.get_current lanza NO_ACTIVE_BUDGET).
    budget: BudgetCurrentOut | None
    upcoming: list[UpcomingItemOut]
    installment_liability: InstallmentLiabilityOut
    # Fase 5 (receivables): stub, siempre null hasta implementar el servicio.
    receivable_cents: int | None = None
