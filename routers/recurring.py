from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.idempotency import IDEMPOTENCY_KEY_HEADER, handle_idempotent_write
from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.recurring import (
    RecurringConfirmRequest,
    RecurringRuleCreate,
    RecurringRuleOut,
    RecurringRuleUpdate,
    SubscriptionsSummaryOut,
)
from schemas.transactions import TransactionOut
from services import recurring_service
from storage.models.user import User

router = APIRouter(prefix="/v1/recurring-rules", tags=["recurring"])
subscriptions_router = APIRouter(prefix="/v1/subscriptions", tags=["recurring"])


@router.get("")
async def list_rules(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[list[RecurringRuleOut]]:
    rules = await recurring_service.list_rules(db, current_user.id)
    return ok([RecurringRuleOut.model_validate(r) for r in rules])


@router.post("")
async def create_rule(
    payload: RecurringRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[RecurringRuleOut]:
    rule = await recurring_service.create_rule(db, current_user.id, payload)
    return ok(RecurringRuleOut.model_validate(rule), "Regla creada.")


@router.get("/upcoming")
async def list_upcoming(
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[list[RecurringRuleOut]]:
    rules = await recurring_service.list_upcoming(db, current_user.id, days)
    return ok([RecurringRuleOut.model_validate(r) for r in rules])


@router.get("/{rule_id}")
async def get_rule(
    rule_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[RecurringRuleOut]:
    rule = await recurring_service.get_rule(db, current_user.id, rule_id)
    return ok(RecurringRuleOut.model_validate(rule))


@router.patch("/{rule_id}")
async def update_rule(
    rule_id: UUID,
    payload: RecurringRuleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[RecurringRuleOut]:
    rule = await recurring_service.update_rule(db, current_user.id, rule_id, payload)
    return ok(RecurringRuleOut.model_validate(rule), "Regla actualizada.")


@router.delete("/{rule_id}")
async def delete_rule(
    rule_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await recurring_service.delete_rule(db, current_user.id, rule_id)
    return ok(None, "Regla eliminada.")


@router.post("/{rule_id}/pause")
async def pause_rule(
    rule_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[RecurringRuleOut]:
    rule = await recurring_service.pause_rule(db, current_user.id, rule_id)
    return ok(RecurringRuleOut.model_validate(rule), "Regla pausada.")


@router.post("/{rule_id}/resume")
async def resume_rule(
    rule_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[RecurringRuleOut]:
    rule = await recurring_service.resume_rule(db, current_user.id, rule_id)
    return ok(RecurringRuleOut.model_validate(rule), "Regla reanudada.")


@router.post("/{rule_id}/skip-next")
async def skip_next(
    rule_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[RecurringRuleOut]:
    rule = await recurring_service.skip_next(db, current_user.id, rule_id)
    return ok(RecurringRuleOut.model_validate(rule), "Próxima ocurrencia saltada.")


@router.post("/{rule_id}/confirm", response_model=None)
async def confirm_rule(
    rule_id: UUID,
    request: Request,
    payload: RecurringConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[TransactionOut] | JSONResponse:
    raw_body = await request.body()

    async def _perform() -> TransactionOut:
        transaction = await recurring_service.confirm_rule(db, current_user.id, rule_id, payload)
        return TransactionOut.model_validate(transaction)

    return await handle_idempotent_write(
        db,
        user_id=current_user.id,
        idempotency_key=request.headers.get(IDEMPOTENCY_KEY_HEADER),
        raw_body=raw_body,
        endpoint=f"POST /v1/recurring-rules/{rule_id}/confirm",
        perform=_perform,
        message="Movimiento confirmado.",
    )


@subscriptions_router.get("/summary")
async def subscriptions_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[SubscriptionsSummaryOut]:
    summary = await recurring_service.subscriptions_summary(db, current_user.id)
    return ok(summary)
