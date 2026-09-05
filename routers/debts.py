from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.idempotency import IDEMPOTENCY_KEY_HEADER, handle_idempotent_write
from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.debts import (
    AmortizationOut,
    DebtCreate,
    DebtOut,
    DebtPaymentCreate,
    DebtPaymentOut,
    DebtUpdate,
)
from services import debt_service
from storage.models.user import User

router = APIRouter(prefix="/v1/debts", tags=["debts"])


@router.get("")
async def list_debts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[list[DebtOut]]:
    debts = await debt_service.list_debts(db, current_user.id)
    return ok([DebtOut.model_validate(d) for d in debts])


@router.post("")
async def create_debt(
    payload: DebtCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[DebtOut]:
    debt = await debt_service.create_debt(db, current_user.id, payload)
    return ok(DebtOut.model_validate(debt), "Deuda creada.")


@router.get("/{debt_id}")
async def get_debt(
    debt_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[DebtOut]:
    debt = await debt_service.get_debt(db, current_user.id, debt_id)
    return ok(DebtOut.model_validate(debt))


@router.patch("/{debt_id}")
async def update_debt(
    debt_id: UUID,
    payload: DebtUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[DebtOut]:
    debt = await debt_service.update_debt(db, current_user.id, debt_id, payload)
    return ok(DebtOut.model_validate(debt), "Deuda actualizada.")


@router.delete("/{debt_id}")
async def delete_debt(
    debt_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await debt_service.delete_debt(db, current_user.id, debt_id)
    return ok(None, "Deuda eliminada.")


@router.get("/{debt_id}/amortization")
async def get_amortization(
    debt_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[AmortizationOut]:
    rows = await debt_service.get_amortization(db, current_user.id, debt_id)
    return ok(AmortizationOut(rows=rows))


@router.post("/{debt_id}/payments", response_model=None)
async def record_payment(
    debt_id: UUID,
    request: Request,
    payload: DebtPaymentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[DebtPaymentOut] | JSONResponse:
    raw_body = await request.body()

    async def _perform() -> DebtPaymentOut:
        payment = await debt_service.record_payment(db, current_user.id, debt_id, payload)
        return DebtPaymentOut.model_validate(payment)

    return await handle_idempotent_write(
        db,
        user_id=current_user.id,
        idempotency_key=request.headers.get(IDEMPOTENCY_KEY_HEADER),
        raw_body=raw_body,
        endpoint=f"POST /v1/debts/{debt_id}/payments",
        perform=_perform,
        message="Pago registrado.",
    )
