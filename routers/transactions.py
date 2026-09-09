from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.idempotency import IDEMPOTENCY_KEY_HEADER, handle_idempotent_write
from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.receipts import ReceiptDownloadOut, ReceiptUploadOut, ReceiptUploadRequest
from schemas.transactions import (
    BulkCategorizeRequest,
    RefundCreate,
    TransactionCreate,
    TransactionCreateResult,
    TransactionListOut,
    TransactionOut,
    TransactionStats,
    TransactionUpdate,
    TransferCreate,
)
from services import receipt_service, transaction_service
from storage.models.user import User

router = APIRouter(prefix="/v1/transactions", tags=["transactions"])


@router.get("")
async def list_transactions(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    account_id: UUID | None = None,
    category_id: UUID | None = None,
    kind: str | None = None,
    q: str | None = None,
    tags: list[str] | None = Query(default=None),
    min_cents: int | None = None,
    max_cents: int | None = None,
    is_reconciled: bool | None = None,
    include_deleted: bool = False,
    cursor: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[TransactionListOut]:
    result = await transaction_service.list_transactions(
        db,
        current_user.id,
        account_id=account_id,
        category_id=category_id,
        kind=kind,
        q=q,
        tags=tags,
        date_from=from_,
        date_to=to,
        min_cents=min_cents,
        max_cents=max_cents,
        is_reconciled=is_reconciled,
        include_deleted=include_deleted,
        cursor=cursor,
        limit=limit,
    )
    return ok(result)


@router.post("", response_model=None)
async def create_transaction(
    request: Request,
    payload: TransactionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[TransactionCreateResult] | JSONResponse:
    raw_body = await request.body()
    return await handle_idempotent_write(
        db,
        user_id=current_user.id,
        idempotency_key=request.headers.get(IDEMPOTENCY_KEY_HEADER),
        raw_body=raw_body,
        endpoint="POST /v1/transactions",
        perform=lambda: transaction_service.create_transaction(db, current_user.id, payload),
        message="Transacción creada.",
    )


@router.post("/transfer", response_model=None)
async def create_transfer(
    request: Request,
    payload: TransferCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[dict[str, Any]] | JSONResponse:
    raw_body = await request.body()

    async def _perform() -> dict[str, Any]:
        out_txn, in_txn = await transaction_service.transfer(db, current_user.id, payload)
        return {
            "out": TransactionOut.model_validate(out_txn),
            "in": TransactionOut.model_validate(in_txn),
        }

    return await handle_idempotent_write(
        db,
        user_id=current_user.id,
        idempotency_key=request.headers.get(IDEMPOTENCY_KEY_HEADER),
        raw_body=raw_body,
        endpoint="POST /v1/transactions/transfer",
        perform=_perform,
        message="Transferencia realizada.",
    )


@router.get("/duplicates")
async def list_duplicate_transactions(
    window_minutes: int = 5,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[list[TransactionOut]]:
    rows = await transaction_service.list_duplicates(
        db, current_user.id, window_minutes=window_minutes
    )
    return ok([TransactionOut.model_validate(r) for r in rows])


@router.get("/stats")
async def get_stats(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    account_id: UUID | None = None,
    category_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[TransactionStats]:
    stats = await transaction_service.get_stats(
        db,
        current_user.id,
        account_id=account_id,
        category_id=category_id,
        date_from=from_,
        date_to=to,
    )
    return ok(stats)


@router.post("/bulk-categorize")
async def bulk_categorize(
    payload: BulkCategorizeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[dict[str, int]]:
    count = await transaction_service.bulk_categorize(
        db, current_user.id, payload.ids, payload.category_id
    )
    return ok({"updated": count}, "Categoría actualizada.")


@router.get("/{transaction_id}")
async def get_transaction(
    transaction_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[TransactionOut]:
    transaction = await transaction_service.get_transaction(db, current_user.id, transaction_id)
    return ok(TransactionOut.model_validate(transaction))


@router.patch("/{transaction_id}")
async def update_transaction(
    transaction_id: UUID,
    payload: TransactionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[TransactionOut]:
    updates = payload.model_dump(exclude_unset=True)
    transaction = await transaction_service.update_transaction(
        db, current_user.id, transaction_id, updates
    )
    return ok(TransactionOut.model_validate(transaction), "Transacción actualizada.")


@router.delete("/{transaction_id}")
async def delete_transaction(
    transaction_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await transaction_service.delete_transaction(db, current_user.id, transaction_id)
    return ok(None, "Transacción eliminada.")


@router.post("/{transaction_id}/restore")
async def restore_transaction(
    transaction_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[TransactionOut]:
    transaction = await transaction_service.restore_transaction(db, current_user.id, transaction_id)
    return ok(TransactionOut.model_validate(transaction), "Transacción restaurada.")


@router.post("/{transaction_id}/refund", response_model=None)
async def refund_transaction(
    transaction_id: UUID,
    request: Request,
    payload: RefundCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[TransactionOut] | JSONResponse:
    raw_body = await request.body()

    async def _perform() -> TransactionOut:
        refund_txn = await transaction_service.refund(db, current_user.id, transaction_id, payload)
        return TransactionOut.model_validate(refund_txn)

    return await handle_idempotent_write(
        db,
        user_id=current_user.id,
        idempotency_key=request.headers.get(IDEMPOTENCY_KEY_HEADER),
        raw_body=raw_body,
        endpoint=f"POST /v1/transactions/{transaction_id}/refund",
        perform=_perform,
        message="Reembolso creado.",
    )


@router.post("/{transaction_id}/receipt")
async def request_receipt_upload(
    transaction_id: UUID,
    payload: ReceiptUploadRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ReceiptUploadOut]:
    result = await receipt_service.request_upload(
        db, current_user.id, transaction_id, payload.content_type
    )
    return ok(result, "URL de subida generada.")


@router.get("/{transaction_id}/receipt")
async def get_receipt_download_url(
    transaction_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ReceiptDownloadOut]:
    result = await receipt_service.get_download_url(db, current_user.id, transaction_id)
    return ok(result)
