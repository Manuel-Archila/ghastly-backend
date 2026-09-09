"""Orquestación de gastos compartidos (caso de negocio 3, PLAN-backend.md §6).

El gasto se registra completo en `transactions`; un `Receivable` guarda lo
que una persona debe de ese gasto — puede haber varios contra la misma
`transaction_id` si se repartió entre varias personas. Liquidar
(`settle`) crea una transacción de INGRESO real (no es un reembolso: la
plata entra de un tercero, no del comercio, así que sí cuenta como
ingreso — a diferencia del caso 5, acá no hay neteo de categoría) ligada
al receivable vía `transactions.receivable_id`, espejo de `refund_of_id`.

`settle` no commitea sola — como `goal_service.contribute`, el router la
pasa por `core/idempotency.py::handle_idempotent_write` porque crea dinero.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ConflictError, NotFoundError, ValidationAppError
from domain.balances import LedgerEntry, signed_delta
from schemas.receivables import ReceivableCreate, ReceivableOut, ReceivableSettle
from services.change_log import record_change
from storage.models.account import Account
from storage.models.receivable import Receivable
from storage.models.transaction import Transaction


def to_out(receivable: Receivable) -> ReceivableOut:
    return ReceivableOut(
        id=receivable.id,
        transaction_id=receivable.transaction_id,
        counterparty=receivable.counterparty,
        amount_cents=receivable.amount_cents,
        settled_at=receivable.settled_at,
        settlement_transaction_id=receivable.settlement_transaction_id,
        status="settled" if receivable.settled_at is not None else "pending",
        created_at=receivable.created_at,
        updated_at=receivable.updated_at,
    )


async def _get_owned(db: AsyncSession, user_id: UUID, receivable_id: UUID) -> Receivable:
    receivable = await db.get(Receivable, receivable_id)
    if receivable is None or receivable.user_id != user_id or receivable.deleted_at is not None:
        raise NotFoundError("El gasto compartido no existe.", code="RECEIVABLE_NOT_FOUND")
    return receivable


async def _get_owned_transaction(
    db: AsyncSession, user_id: UUID, transaction_id: UUID
) -> Transaction:
    transaction = await db.get(Transaction, transaction_id)
    if transaction is None or transaction.user_id != user_id or transaction.deleted_at is not None:
        raise NotFoundError("La transacción no existe.", code="TRANSACTION_NOT_FOUND")
    return transaction


async def create_receivable(db: AsyncSession, user_id: UUID, data: ReceivableCreate) -> Receivable:
    transaction = await _get_owned_transaction(db, user_id, data.transaction_id)
    if transaction.kind != "expense":
        raise ValidationAppError(
            "Un gasto compartido solo puede registrarse sobre un gasto.",
            field="transaction_id",
            code="RECEIVABLE_REQUIRES_EXPENSE_TRANSACTION",
        )

    already_claimed_stmt = select(func.coalesce(func.sum(Receivable.amount_cents), 0)).where(
        Receivable.transaction_id == data.transaction_id, Receivable.deleted_at.is_(None)
    )
    already_claimed = (await db.execute(already_claimed_stmt)).scalar_one()
    if already_claimed + data.amount_cents > transaction.amount_cents:
        raise ValidationAppError(
            "La suma de lo que deben por este gasto no puede superar el monto del gasto.",
            field="amount_cents",
            code="RECEIVABLE_EXCEEDS_TRANSACTION_AMOUNT",
        )

    receivable = Receivable(
        id=data.id,
        user_id=user_id,
        transaction_id=data.transaction_id,
        counterparty=data.counterparty,
        amount_cents=data.amount_cents,
    )
    db.add(receivable)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            "Ya existe un gasto compartido con ese id.", code="RECEIVABLE_ID_TAKEN"
        ) from exc

    await record_change(
        db,
        user_id=user_id,
        entity_type="receivable",
        op="upsert",
        entity=receivable,
        payload=to_out(receivable).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(receivable)
    return receivable


async def list_receivables(db: AsyncSession, user_id: UUID) -> list[Receivable]:
    result = await db.execute(
        select(Receivable)
        .where(Receivable.user_id == user_id, Receivable.deleted_at.is_(None))
        .order_by(Receivable.created_at.desc())
    )
    return list(result.scalars().all())


async def get_receivable(db: AsyncSession, user_id: UUID, receivable_id: UUID) -> Receivable:
    return await _get_owned(db, user_id, receivable_id)


async def total_pending_cents(db: AsyncSession, user_id: UUID) -> int:
    """Lo que le deben al usuario en total — el "Por cobrar" del dashboard,
    aparte del patrimonio (caso de negocio 3)."""
    stmt = select(func.coalesce(func.sum(Receivable.amount_cents), 0)).where(
        Receivable.user_id == user_id,
        Receivable.deleted_at.is_(None),
        Receivable.settled_at.is_(None),
    )
    return int((await db.execute(stmt)).scalar_one())


async def settle(
    db: AsyncSession, user_id: UUID, receivable_id: UUID, data: ReceivableSettle
) -> Receivable:
    receivable = await _get_owned(db, user_id, receivable_id)
    if receivable.settled_at is not None:
        raise ConflictError(
            "Este gasto compartido ya se liquidó.", code="RECEIVABLE_ALREADY_SETTLED"
        )

    account = await db.get(Account, data.account_id)
    if account is None or account.user_id != user_id or account.deleted_at is not None:
        raise NotFoundError("La cuenta no existe.", code="ACCOUNT_NOT_FOUND")
    if account.is_archived:
        raise ValidationAppError("La cuenta está archivada.", code="ACCOUNT_ARCHIVED")

    now = datetime.now(UTC)
    income_txn = Transaction(
        id=data.id,
        user_id=user_id,
        account_id=account.id,
        kind="income",
        amount_cents=receivable.amount_cents,
        currency=account.currency,
        date=data.date,
        description=f"Cobro de {receivable.counterparty}",
        receivable_id=receivable.id,
        created_at=now,
        updated_at=now,
    )
    db.add(income_txn)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            "Ya existe una transacción con ese id.", code="TRANSACTION_ID_TAKEN"
        ) from exc

    account.current_balance_cents += signed_delta(
        LedgerEntry(kind="income", amount_cents=receivable.amount_cents),
        account.type,  # type: ignore[arg-type]
    )
    account.updated_at = now

    receivable.settled_at = now
    receivable.settlement_transaction_id = income_txn.id
    receivable.updated_at = now

    await record_change(
        db,
        user_id=user_id,
        entity_type="transaction",
        op="upsert",
        entity=income_txn,
        payload={"id": str(income_txn.id), "receivable_id": str(receivable.id)},
    )
    await record_change(
        db,
        user_id=user_id,
        entity_type="receivable",
        op="upsert",
        entity=receivable,
        payload=to_out(receivable).model_dump(mode="json"),
    )
    return receivable
