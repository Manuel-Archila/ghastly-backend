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
    source_type: Literal["installment", "recurring"]
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
