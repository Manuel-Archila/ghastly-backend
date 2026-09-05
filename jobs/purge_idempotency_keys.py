"""Job horario: purga `idempotency_keys` y `processed_mutations` con más de 24h."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete

from storage.db import get_session_factory
from storage.models.sync import IdempotencyKey
from storage.models.sync_mutation import ProcessedMutation

logger = structlog.get_logger("jobs.purge_idempotency_keys")

TTL = timedelta(hours=24)


async def run() -> None:
    cutoff = datetime.now(UTC) - TTL
    session_factory = get_session_factory()
    async with session_factory() as db:
        keys_result = await db.execute(
            delete(IdempotencyKey).where(IdempotencyKey.created_at < cutoff)
        )
        mutations_result = await db.execute(
            delete(ProcessedMutation).where(ProcessedMutation.created_at < cutoff)
        )
        await db.commit()

    logger.info(
        "purge_idempotency_keys_done",
        idempotency_keys=keys_result.rowcount,  # type: ignore[attr-defined]
        processed_mutations=mutations_result.rowcount,  # type: ignore[attr-defined]
    )
