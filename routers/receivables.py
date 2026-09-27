from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.idempotency import IDEMPOTENCY_KEY_HEADER, handle_idempotent_write
from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.receivables import (
    ReceivableCreate,
    ReceivableOut,
    ReceivableSettle,
    ReceivableUpdate,
)
from services import receivable_service
from storage.models.user import User

router = APIRouter(prefix="/v1/receivables", tags=["receivables"])


@router.get("")
async def list_receivables(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[list[ReceivableOut]]:
    receivables = await receivable_service.list_receivables(db, current_user.id)
    return ok([receivable_service.to_out(r) for r in receivables])


@router.post("")
async def create_receivable(
    payload: ReceivableCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ReceivableOut]:
    receivable = await receivable_service.create_receivable(db, current_user.id, payload)
    return ok(receivable_service.to_out(receivable), "Gasto compartido registrado.")


@router.get("/{receivable_id}")
async def get_receivable(
    receivable_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ReceivableOut]:
    receivable = await receivable_service.get_receivable(db, current_user.id, receivable_id)
    return ok(receivable_service.to_out(receivable))


@router.patch("/{receivable_id}")
async def update_receivable(
    receivable_id: UUID,
    payload: ReceivableUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ReceivableOut]:
    receivable = await receivable_service.update_receivable(
        db, current_user.id, receivable_id, payload
    )
    return ok(receivable_service.to_out(receivable), "Gasto compartido actualizado.")


@router.delete("/{receivable_id}")
async def delete_receivable(
    receivable_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await receivable_service.delete_receivable(db, current_user.id, receivable_id)
    return ok(None, "Gasto compartido eliminado.")


@router.post("/{receivable_id}/settle", response_model=None)
async def settle(
    receivable_id: UUID,
    request: Request,
    payload: ReceivableSettle,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ReceivableOut] | JSONResponse:
    raw_body = await request.body()

    async def _perform() -> ReceivableOut:
        receivable = await receivable_service.settle(db, current_user.id, receivable_id, payload)
        return receivable_service.to_out(receivable)

    return await handle_idempotent_write(
        db,
        user_id=current_user.id,
        idempotency_key=request.headers.get(IDEMPOTENCY_KEY_HEADER),
        raw_body=raw_body,
        endpoint=f"POST /v1/receivables/{receivable_id}/settle",
        perform=_perform,
        message="Gasto compartido liquidado.",
    )
