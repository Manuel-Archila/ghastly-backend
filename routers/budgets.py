from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.budgets import (
    BudgetCreate,
    BudgetCurrentOut,
    BudgetHistoryOut,
    BudgetItemCreate,
    BudgetItemOut,
    BudgetItemUpdate,
    BudgetOut,
    BudgetUpdate,
    ClosePeriodResult,
)
from services import budget_service
from storage.models.user import User

router = APIRouter(prefix="/v1/budgets", tags=["budgets"])


@router.get("")
async def list_budgets(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[list[BudgetOut]]:
    budgets = await budget_service.list_budgets(db, current_user.id)
    return ok([BudgetOut.model_validate(b) for b in budgets])


@router.post("")
async def create_budget(
    payload: BudgetCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[BudgetOut]:
    budget = await budget_service.create_budget(db, current_user.id, payload)
    return ok(BudgetOut.model_validate(budget), "Presupuesto creado.")


# Ruta literal antes que "/{budget_id}" (mismo motivo que accounts.py/categories.py).
@router.get("/current")
async def get_current(
    month: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[BudgetCurrentOut]:
    result = await budget_service.get_current(db, current_user.id, month)
    return ok(result)


@router.get("/{budget_id}")
async def get_budget(
    budget_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[BudgetOut]:
    budget = await budget_service.get_budget(db, current_user.id, budget_id)
    return ok(BudgetOut.model_validate(budget))


@router.patch("/{budget_id}")
async def update_budget(
    budget_id: UUID,
    payload: BudgetUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[BudgetOut]:
    budget = await budget_service.update_budget(db, current_user.id, budget_id, payload)
    return ok(BudgetOut.model_validate(budget), "Presupuesto actualizado.")


@router.delete("/{budget_id}")
async def delete_budget(
    budget_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await budget_service.delete_budget(db, current_user.id, budget_id)
    return ok(None, "Presupuesto eliminado.")


@router.post("/{budget_id}/items")
async def add_item(
    budget_id: UUID,
    payload: BudgetItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[BudgetItemOut]:
    item = await budget_service.add_item(db, current_user.id, budget_id, payload)
    return ok(BudgetItemOut.model_validate(item), "Categoría asignada al presupuesto.")


@router.patch("/{budget_id}/items/{item_id}")
async def update_item(
    budget_id: UUID,
    item_id: UUID,
    payload: BudgetItemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[BudgetItemOut]:
    item = await budget_service.update_item(db, current_user.id, budget_id, item_id, payload)
    return ok(BudgetItemOut.model_validate(item), "Ítem actualizado.")


@router.post("/{budget_id}/copy-from-previous")
async def copy_from_previous(
    budget_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[BudgetOut]:
    budget = await budget_service.copy_from_previous(db, current_user.id, budget_id)
    return ok(BudgetOut.model_validate(budget), "Presupuesto copiado del mes anterior.")


@router.post("/{budget_id}/close-period")
async def close_period(
    budget_id: UUID,
    month: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ClosePeriodResult]:
    result = await budget_service.close_period(db, current_user.id, budget_id, month)
    return ok(result, "Período cerrado.")


@router.get("/{budget_id}/history")
async def get_history(
    budget_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[BudgetHistoryOut]:
    result = await budget_service.get_history(db, current_user.id, budget_id)
    return ok(result)
