from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.idempotency import IDEMPOTENCY_KEY_HEADER, handle_idempotent_write
from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.installments import (
    InstallmentLiabilityMonth,
    InstallmentLiabilityOut,
    InstallmentOut,
    InstallmentPayRequest,
    InstallmentPlanCreate,
    InstallmentPlanOut,
    InstallmentPlanUpdate,
    InstallmentPlanWithScheduleOut,
)
from services import installment_service
from storage.models.user import User

plans_router = APIRouter(prefix="/v1/installment-plans", tags=["installments"])
installments_router = APIRouter(prefix="/v1/installments", tags=["installments"])


@plans_router.get("")
async def list_plans(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[list[InstallmentPlanOut]]:
    plans = await installment_service.list_plans(db, current_user.id)
    return ok([InstallmentPlanOut.model_validate(p) for p in plans])


@plans_router.post("")
async def create_plan(
    payload: InstallmentPlanCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[InstallmentPlanOut]:
    plan = await installment_service.create_plan(db, current_user.id, payload)
    return ok(InstallmentPlanOut.model_validate(plan), "Plan de cuotas creado.")


@plans_router.get("/{plan_id}")
async def get_plan(
    plan_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[InstallmentPlanOut]:
    plan = await installment_service.get_plan(db, current_user.id, plan_id)
    return ok(InstallmentPlanOut.model_validate(plan))


@plans_router.patch("/{plan_id}")
async def update_plan(
    plan_id: UUID,
    payload: InstallmentPlanUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[InstallmentPlanOut]:
    plan = await installment_service.update_plan(db, current_user.id, plan_id, payload)
    return ok(InstallmentPlanOut.model_validate(plan), "Plan actualizado.")


@plans_router.delete("/{plan_id}")
async def delete_plan(
    plan_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await installment_service.delete_plan(db, current_user.id, plan_id)
    return ok(None, "Plan cancelado.")


@plans_router.get("/{plan_id}/schedule")
async def get_schedule(
    plan_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[InstallmentPlanWithScheduleOut]:
    plan = await installment_service.get_plan(db, current_user.id, plan_id)
    schedule = await installment_service.get_schedule(db, current_user.id, plan_id)
    data = InstallmentPlanWithScheduleOut(
        **InstallmentPlanOut.model_validate(plan).model_dump(),
        schedule=[InstallmentOut.model_validate(i) for i in schedule],
    )
    return ok(data)


@installments_router.post("/{installment_id}/pay", response_model=None)
async def pay_installment(
    installment_id: UUID,
    request: Request,
    payload: InstallmentPayRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[InstallmentOut] | JSONResponse:
    raw_body = await request.body()

    async def _perform() -> InstallmentOut:
        installment = await installment_service.pay_installment(
            db, current_user.id, installment_id, payload
        )
        return InstallmentOut.model_validate(installment)

    return await handle_idempotent_write(
        db,
        user_id=current_user.id,
        idempotency_key=request.headers.get(IDEMPOTENCY_KEY_HEADER),
        raw_body=raw_body,
        endpoint=f"POST /v1/installments/{installment_id}/pay",
        perform=_perform,
        message="Cuota pagada.",
    )


@installments_router.get("/upcoming")
async def list_upcoming(
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[list[InstallmentOut]]:
    installments = await installment_service.list_upcoming(db, current_user.id, days)
    return ok([InstallmentOut.model_validate(i) for i in installments])


@installments_router.get("/liability")
async def get_liability(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[InstallmentLiabilityOut]:
    total, by_month = await installment_service.get_liability(db, current_user.id)
    return ok(
        InstallmentLiabilityOut(
            total_pending_cents=total,
            by_month=[InstallmentLiabilityMonth(month=m, amount_cents=a) for m, a in by_month],
        )
    )
