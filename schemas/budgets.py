from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from schemas.common import PatchModel

IncomeBasis = Literal["fixed", "previous_month", "avg_3m"]


class BudgetItemCreate(BaseModel):
    id: UUID
    category_id: UUID
    amount_cents: int = Field(ge=0)
    rollover_enabled: bool | None = None
    sort_order: int = 0


class BudgetCreate(BaseModel):
    id: UUID
    name: str = Field(min_length=1, max_length=255)
    period_type: Literal["monthly", "weekly", "custom"] = "monthly"
    rollover_enabled: bool = False
    global_limit_cents: int | None = Field(default=None, ge=0)
    income_basis: IncomeBasis = "fixed"
    fixed_income_cents: int | None = Field(default=None, ge=0)
    items: list[BudgetItemCreate] = Field(default_factory=list)


class BudgetUpdate(PatchModel):
    non_nullable = frozenset({"name", "is_active", "rollover_enabled", "income_basis"})
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None
    rollover_enabled: bool | None = None
    global_limit_cents: int | None = Field(default=None, ge=0)
    income_basis: IncomeBasis | None = None
    fixed_income_cents: int | None = Field(default=None, ge=0)


class BudgetItemUpdate(PatchModel):
    non_nullable = frozenset({"category_id", "amount_cents", "sort_order"})
    category_id: UUID | None = None
    amount_cents: int | None = Field(default=None, ge=0)
    rollover_enabled: bool | None = None
    sort_order: int | None = None


class BudgetItemOut(BaseModel):
    id: UUID
    budget_id: UUID
    category_id: UUID
    amount_cents: int
    rollover_enabled: bool | None
    sort_order: int

    model_config = {"from_attributes": True}


class BudgetWarningOut(BaseModel):
    """Advertencia, no error (regla de negocio 7): la escritura se aplicó igual."""

    code: Literal["CHILDREN_EXCEED_PARENT"]
    message: str
    parent_item_id: UUID
    parent_category_id: UUID
    parent_cents: int
    children_cents: int
    excess_cents: int


class BudgetItemWriteOut(BudgetItemOut):
    warning: BudgetWarningOut | None = None


class BudgetOut(BaseModel):
    id: UUID
    name: str
    period_type: str
    is_active: bool
    rollover_enabled: bool
    global_limit_cents: int | None
    income_basis: IncomeBasis
    fixed_income_cents: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CategoryProgressOut(BaseModel):
    category_id: UUID
    category_name: str
    budgeted_cents: int
    rollover_in_cents: int
    spent_cents: int
    available_cents: int
    percent_consumed: int
    projected_cents: int
    suggested_daily_pace_cents: int
    # Jerarquía derivada de `categories.parent_id`. `parent_category_id` solo se
    # llena si la categoría padre TAMBIÉN tiene ítem en este presupuesto; si no,
    # el ítem es raíz. `spent_cents` de un padre ya incluye a sus subcategorías.
    parent_category_id: UUID | None = None
    children_budgeted_cents: int = 0
    children_excess_cents: int = 0  # > 0: los hijos suman más que el tope del padre


class UnbudgetedCategoryOut(BaseModel):
    category_id: UUID
    category_name: str
    spent_cents: int


class BudgetCurrentOut(BaseModel):
    month: str
    is_closed: bool
    expected_income_cents: int | None
    total_budgeted_cents: int
    total_spent_cents: int
    total_available_cents: int
    global_limit_cents: int | None
    global_projected_cents: int
    items: list[CategoryProgressOut]
    unbudgeted: list[UnbudgetedCategoryOut]


class ClosePeriodResult(BaseModel):
    month: str
    items: list[CategoryProgressOut]


class BudgetHistoryPeriodOut(BaseModel):
    month: str
    closed_at: datetime
    items: list[CategoryProgressOut]


class BudgetHistoryOut(BaseModel):
    periods: list[BudgetHistoryPeriodOut]
