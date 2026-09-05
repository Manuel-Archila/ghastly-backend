"""Escribe en `change_log` — el cursor de sincronización (PLAN-backend §7).

Cada service que muta una entidad de dominio (`Account`, `Category`,
`Transaction`, ...) llama a `record_change` en la MISMA transacción de DB
que hace el `INSERT`/`UPDATE`. El `server_seq` que resulta se guarda
también en la fila de la entidad — así `/sync/pull` puede filtrar por
`server_seq` sin tener que unir contra `change_log` para saber "qué es lo
último que cambió".
"""

from __future__ import annotations

from typing import Any, Literal, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from storage.models.sync import ChangeLog

Op = Literal["upsert", "delete"]


class _HasIdAndServerSeq(Protocol):
    id: UUID
    server_seq: int


async def record_change(
    db: AsyncSession,
    *,
    user_id: UUID,
    entity_type: str,
    op: Op,
    entity: _HasIdAndServerSeq,
    payload: dict[str, Any],
    device_id: UUID | None = None,
) -> None:
    change = ChangeLog(
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity.id,
        op=op,
        payload=payload,
        device_id=device_id,
    )
    db.add(change)
    await db.flush()
    entity.server_seq = change.server_seq
