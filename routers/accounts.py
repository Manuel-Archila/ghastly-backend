from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.accounts import AccountAdjustRequest, AccountCreate, AccountOut, AccountUpdate
from schemas.common import ReorderRequest
from schemas.transactions import TransactionOut
from services import account_service
from storage.models.user import User

router = APIRouter(prefix="/v1/accounts", tags=["accounts"])


@router.get("")
async def list_accounts(
    include_archived: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[list[AccountOut]]:
    accounts = await account_service.list_accounts(
        db, current_user.id, include_archived=include_archived
    )
    return ok([AccountOut.model_validate(a) for a in accounts])


@router.post("")
async def create_account(
    payload: AccountCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[AccountOut]:
    account = await account_service.create_account(db, current_user.id, payload)
    return ok(AccountOut.model_validate(account), "Cuenta creada.")


# Rutas literales ANTES que "/{account_id}": si no, FastAPI intenta parsear
# "reorder" como un UUID de cuenta.
@router.patch("/reorder")
async def reorder_accounts(
    payload: ReorderRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await account_service.reorder_accounts(db, current_user.id, payload.ids)
    return ok(None, "Orden actualizado.")


@router.get("/{account_id}")
async def get_account(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[AccountOut]:
    account = await account_service.get_account(db, current_user.id, account_id)
    return ok(AccountOut.model_validate(account))


@router.patch("/{account_id}")
async def update_account(
    account_id: UUID,
    payload: AccountUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[AccountOut]:
    account = await account_service.update_account(db, current_user.id, account_id, payload)
    return ok(AccountOut.model_validate(account), "Cuenta actualizada.")


@router.delete("/{account_id}")
async def archive_account(
    account_id: UUID,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await account_service.archive_account(db, current_user.id, account_id, force=force)
    return ok(None, "Cuenta archivada.")


@router.post("/{account_id}/recalculate")
async def recalculate_account(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[AccountOut]:
    account = await account_service.recalculate_account(db, current_user.id, account_id)
    return ok(AccountOut.model_validate(account), "Saldo recalculado.")


@router.post("/{account_id}/adjust")
async def adjust_account(
    account_id: UUID,
    payload: AccountAdjustRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[TransactionOut]:
    transaction = await account_service.adjust_account(db, current_user.id, account_id, payload)
    return ok(TransactionOut.model_validate(transaction), "Saldo ajustado.")
