"""Orquestación de recibos (fotos de comprobante) — PLAN-backend.md §14.

El backend nunca ve los bytes del archivo: `request_upload` firma una URL
de subida (el cliente sube directo a R2 con ella) y `get_download_url`
firma una de bajada para verlo. `receipt_key` es determinístico por
transacción (`receipts/{user_id}/{transaction_id}`) — pedir upload de
nuevo para la misma transacción reemplaza el recibo anterior en vez de
acumular objetos huérfanos en el bucket.

`receipt_key` se guarda en la transacción de forma optimista, al pedir la
URL de subida, no al confirmarse la subida — igual que otras banderas
"advertir, no bloquear" del proyecto (CLAUDE.md regla 7): construir un
callback de confirmación desde R2 es infraestructura de más para una app
de un solo usuario, y si la subida falla el cliente simplemente vuelve a
pedir la URL.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import NotFoundError
from core.object_storage import (
    PRESIGNED_URL_EXPIRES_SECONDS,
    generate_download_url,
    generate_upload_url,
)
from schemas.receipts import ReceiptDownloadOut, ReceiptUploadOut
from schemas.transactions import TransactionOut
from services.change_log import record_change
from storage.models.transaction import Transaction


def _receipt_key(user_id: UUID, transaction_id: UUID) -> str:
    return f"receipts/{user_id}/{transaction_id}"


async def _get_owned(db: AsyncSession, user_id: UUID, transaction_id: UUID) -> Transaction:
    transaction = await db.get(Transaction, transaction_id)
    if transaction is None or transaction.user_id != user_id or transaction.deleted_at is not None:
        raise NotFoundError("La transacción no existe.", code="TRANSACTION_NOT_FOUND")
    return transaction


async def request_upload(
    db: AsyncSession, user_id: UUID, transaction_id: UUID, content_type: str
) -> ReceiptUploadOut:
    transaction = await _get_owned(db, user_id, transaction_id)
    key = _receipt_key(user_id, transaction_id)
    upload_url = generate_upload_url(key, content_type=content_type)

    transaction.receipt_key = key
    await record_change(
        db,
        user_id=user_id,
        entity_type="transaction",
        op="upsert",
        entity=transaction,
        payload=TransactionOut.model_validate(transaction).model_dump(mode="json"),
    )
    await db.commit()

    return ReceiptUploadOut(
        upload_url=upload_url, receipt_key=key, expires_in=PRESIGNED_URL_EXPIRES_SECONDS
    )


async def get_download_url(
    db: AsyncSession, user_id: UUID, transaction_id: UUID
) -> ReceiptDownloadOut:
    transaction = await _get_owned(db, user_id, transaction_id)
    if transaction.receipt_key is None:
        raise NotFoundError("Esta transacción no tiene un recibo.", code="RECEIPT_NOT_FOUND")

    download_url = generate_download_url(transaction.receipt_key)
    return ReceiptDownloadOut(download_url=download_url, expires_in=PRESIGNED_URL_EXPIRES_SECONDS)
