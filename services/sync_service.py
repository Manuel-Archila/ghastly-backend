"""Orquestación de `/sync` (PLAN-backend §7).

Reusa los mismos `*_service.*` que usan los routers REST para crear,
actualizar, archivar y borrar — la lógica nueva acá es la resolución de
conflictos de §7 y el dedup por `client_mutation_id` (reenviar el mismo
lote es no-op).

Limitación documentada: una transferencia (`kind='transfer'`) no se puede
CREAR por este canal — son dos filas atómicas y el par se rompe si una
llega en un lote y la otra en el siguiente. Para eso existe
`POST /transactions/transfer`, que el cliente debe llamar online. Sí se
puede actualizar o borrar una pata de una transferencia ya existente.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AppError, NotFoundError
from schemas.accounts import AccountCreate, AccountOut, AccountUpdate
from schemas.categories import CategoryCreate, CategoryOut, CategoryUpdate
from schemas.sync import SyncChangeOut, SyncConflictOut, SyncMutationIn, SyncPullOut, SyncPushResult
from schemas.transactions import TransactionCreate, TransactionOut
from services import account_service, category_service, transaction_service
from storage.models.auth import Device
from storage.models.sync import ChangeLog
from storage.models.sync_mutation import ProcessedMutation

ACCOUNT_EDITABLE_FIELDS = {
    "name",
    "institution",
    "last_four",
    "color",
    "icon",
    "sort_order",
    "credit_limit_cents",
    "statement_day",
    "payment_due_day",
    "interest_rate",
}
CATEGORY_EDITABLE_FIELDS = {"name", "icon", "color", "is_tax_deductible", "sort_order"}
TRANSACTION_EDITABLE_FIELDS = {
    "category_id",
    "date",
    "description",
    "merchant",
    "notes",
    "is_reconciled",
    "is_tax_relevant",
    "is_extraordinary",
    "tags",
}

# (aplicado, razón_de_conflicto, payload_del_servidor_si_hay_conflicto)
_MutationOutcome = tuple[bool, str | None, dict[str, Any] | None]


async def pull(db: AsyncSession, user_id: UUID, *, since: int, limit: int = 500) -> SyncPullOut:
    result = await db.execute(
        select(ChangeLog)
        .where(ChangeLog.user_id == user_id, ChangeLog.server_seq > since)
        .order_by(ChangeLog.server_seq)
        .limit(limit + 1)
    )
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
    next_seq = rows[-1].server_seq if rows else since
    changes = [
        SyncChangeOut(
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            op=r.op,
            payload=r.payload,
            server_seq=r.server_seq,
        )
        for r in rows
    ]
    return SyncPullOut(changes=changes, next_seq=next_seq, has_more=has_more)


async def get_status(db: AsyncSession, user_id: UUID) -> int:
    result = await db.execute(
        select(func.max(ChangeLog.server_seq)).where(ChangeLog.user_id == user_id)
    )
    return result.scalar_one_or_none() or 0


async def _apply_account(
    db: AsyncSession, user_id: UUID, mutation: SyncMutationIn
) -> _MutationOutcome:
    try:
        account = await account_service.get_account(db, user_id, mutation.entity_id)
    except NotFoundError:
        account = None

    if mutation.op == "delete":
        if account is None or account.is_archived:
            return True, None, None
        await account_service.archive_account(db, user_id, mutation.entity_id)
        return True, None, None

    if account is None:
        data = AccountCreate.model_validate({**mutation.payload, "id": mutation.entity_id})
        await account_service.create_account(db, user_id, data)
        return True, None, None

    if account.is_archived:
        return (
            False,
            "DELETED_ON_SERVER",
            AccountOut.model_validate(account).model_dump(mode="json"),
        )

    if mutation.client_updated_at <= account.updated_at:
        return False, "STALE_UPDATE", AccountOut.model_validate(account).model_dump(mode="json")

    filtered = {k: v for k, v in mutation.payload.items() if k in ACCOUNT_EDITABLE_FIELDS}
    await account_service.update_account(db, user_id, mutation.entity_id, AccountUpdate(**filtered))
    return True, None, None


async def _apply_category(
    db: AsyncSession, user_id: UUID, mutation: SyncMutationIn
) -> _MutationOutcome:
    try:
        category = await category_service.get_category(db, user_id, mutation.entity_id)
    except NotFoundError:
        category = None

    if mutation.op == "delete":
        if category is None or category.is_archived:
            return True, None, None
        await category_service.archive_category(db, user_id, mutation.entity_id)
        return True, None, None

    if category is None:
        data = CategoryCreate.model_validate({**mutation.payload, "id": mutation.entity_id})
        await category_service.create_category(db, user_id, data)
        return True, None, None

    if category.is_archived:
        return (
            False,
            "DELETED_ON_SERVER",
            CategoryOut.model_validate(category).model_dump(mode="json"),
        )

    if mutation.client_updated_at <= category.updated_at:
        return False, "STALE_UPDATE", CategoryOut.model_validate(category).model_dump(mode="json")

    filtered = {k: v for k, v in mutation.payload.items() if k in CATEGORY_EDITABLE_FIELDS}
    await category_service.update_category(
        db, user_id, mutation.entity_id, CategoryUpdate(**filtered)
    )
    return True, None, None


async def _apply_transaction(
    db: AsyncSession, user_id: UUID, mutation: SyncMutationIn
) -> _MutationOutcome:
    try:
        transaction = await transaction_service.get_transaction(db, user_id, mutation.entity_id)
    except NotFoundError:
        transaction = None

    if mutation.op == "delete":
        if transaction is None or transaction.deleted_at is not None:
            return True, None, None
        await transaction_service.delete_transaction(db, user_id, mutation.entity_id)
        return True, None, None

    if transaction is None:
        if mutation.payload.get("kind") == "transfer":
            return False, "TRANSFER_NOT_SUPPORTED_VIA_SYNC_PUSH", None
        data = TransactionCreate.model_validate({**mutation.payload, "id": mutation.entity_id})
        await transaction_service.create_transaction(db, user_id, data)
        await db.commit()  # create_transaction no commitea (ver core/idempotency.py)
        return True, None, None

    if transaction.deleted_at is not None:
        return (
            False,
            "DELETED_ON_SERVER",
            TransactionOut.model_validate(transaction).model_dump(mode="json"),
        )

    if mutation.client_updated_at <= transaction.updated_at:
        return (
            False,
            "STALE_UPDATE",
            TransactionOut.model_validate(transaction).model_dump(mode="json"),
        )

    filtered = {k: v for k, v in mutation.payload.items() if k in TRANSACTION_EDITABLE_FIELDS}
    await transaction_service.update_transaction(db, user_id, mutation.entity_id, filtered)
    return True, None, None


_APPLIERS = {
    "account": _apply_account,
    "category": _apply_category,
    "transaction": _apply_transaction,
}


async def push(
    db: AsyncSession, user_id: UUID, device_id: UUID, mutations: list[SyncMutationIn]
) -> SyncPushResult:
    device = await db.get(Device, device_id)
    if device is None or device.user_id != user_id:
        raise NotFoundError("El dispositivo no existe.", code="DEVICE_NOT_FOUND")

    applied: list[UUID] = []
    conflicts: list[SyncConflictOut] = []

    for mutation in mutations:
        existing = await db.execute(
            select(ProcessedMutation).where(
                ProcessedMutation.user_id == user_id,
                ProcessedMutation.client_mutation_id == mutation.client_mutation_id,
            )
        )
        processed = existing.scalar_one_or_none()
        if processed is not None:
            # Reenviar el mismo lote es no-op: se devuelve el resultado ya calculado.
            if processed.outcome == "applied":
                applied.append(mutation.client_mutation_id)
            else:
                conflicts.append(
                    SyncConflictOut(
                        client_mutation_id=mutation.client_mutation_id,
                        entity_id=mutation.entity_id,
                        reason=processed.reason or "UNKNOWN",
                        server_payload=processed.server_payload,
                    )
                )
            continue

        applier = _APPLIERS[mutation.entity_type]
        try:
            was_applied, reason, server_payload = await applier(db, user_id, mutation)
        except AppError as exc:
            # Un error de negocio en UNA mutación (p. ej. cuenta inexistente,
            # categoría de otro tipo) es un conflicto de esa mutación, no un
            # 500 que tumbe el lote completo — un push de 2000 debe tolerarlo.
            await db.rollback()
            was_applied, reason, server_payload = False, exc.code, None

        db.add(
            ProcessedMutation(
                user_id=user_id,
                client_mutation_id=mutation.client_mutation_id,
                entity_type=mutation.entity_type,
                entity_id=mutation.entity_id,
                outcome="applied" if was_applied else "conflict",
                reason=reason,
                server_payload=server_payload,
            )
        )
        await db.commit()

        if was_applied:
            applied.append(mutation.client_mutation_id)
        else:
            conflicts.append(
                SyncConflictOut(
                    client_mutation_id=mutation.client_mutation_id,
                    entity_id=mutation.entity_id,
                    reason=reason or "UNKNOWN",
                    server_payload=server_payload,
                )
            )

    next_seq = await get_status(db, user_id)
    device.last_sync_seq = next_seq
    device.last_seen_at = datetime.now(UTC)
    await db.commit()
    return SyncPushResult(applied=applied, conflicts=conflicts, next_seq=next_seq)
