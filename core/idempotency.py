"""Soporte de `Idempotency-Key` (CLAUDE.md: todo POST que crea dinero lo exige).

`handle_idempotent_write` es el punto único que usan los tres endpoints que
mueven dinero (`POST /transactions`, `.../transfer`, `.../refund`): si el
mismo `Idempotency-Key` ya se procesó con el mismo cuerpo, devuelve la
respuesta guardada sin ejecutar nada de nuevo. Si el cuerpo es distinto,
es un error del cliente (reusó la key para otra cosa), no un reintento.

Nota de diseño: para que la fila en `idempotency_keys` quede en la MISMA
transacción de DB que el movimiento de dinero, los `service.*` que crean
transacciones (`create_transaction`, `transfer`, `refund`) NO hacen commit
por su cuenta — devuelven el trabajo pendiente de `flush()` y es este
módulo el que hace el único `commit()`, después de escribir la fila de
idempotencia. Los demás services (accounts, categories, auth) sí
commitean solos porque no participan de este mecanismo.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ConflictError, ValidationAppError
from core.response import ApiResponse, ok
from storage.models.sync import IdempotencyKey

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"


def compute_request_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


async def _get_cached(
    db: AsyncSession, user_id: UUID, key: str, endpoint: str, request_hash: str
) -> tuple[int, dict[str, Any]] | None:
    result = await db.execute(
        select(IdempotencyKey).where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    if row.endpoint != endpoint or row.request_hash != request_hash:
        raise ConflictError(
            "Ese Idempotency-Key ya se usó con una solicitud distinta.",
            code="IDEMPOTENCY_KEY_REUSED",
        )
    return row.status_code, row.response_body


async def handle_idempotent_write(
    db: AsyncSession,
    *,
    user_id: UUID,
    idempotency_key: str | None,
    raw_body: bytes,
    endpoint: str,
    perform: Callable[[], Awaitable[Any]],
    message: str,
    status_code: int = 200,
) -> ApiResponse[Any] | JSONResponse:
    if not idempotency_key:
        raise ValidationAppError(
            "Falta el header Idempotency-Key.", code="IDEMPOTENCY_KEY_REQUIRED"
        )

    request_hash = compute_request_hash(raw_body)
    cached = await _get_cached(db, user_id, idempotency_key, endpoint, request_hash)
    if cached is not None:
        cached_status, cached_body = cached
        return JSONResponse(status_code=cached_status, content=cached_body)

    data = await perform()
    envelope = ok(data, message)
    body = jsonable_encoder(envelope)

    db.add(
        IdempotencyKey(
            user_id=user_id,
            key=idempotency_key,
            endpoint=endpoint,
            request_hash=request_hash,
            response_body=body,
            status_code=status_code,
        )
    )
    await db.commit()
    return envelope
