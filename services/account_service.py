"""Orquestación de cuentas: DB + `domain/balances.py`. El signo del saldo se
decide en el dominio; este service solo junta filas y llama a `record_change`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ConflictError, NotFoundError, ValidationAppError
from domain.balances import LedgerEntry, compute_balance, resolve_adjustment
from schemas.accounts import AccountAdjustRequest, AccountCreate, AccountOut, AccountUpdate
from services.change_log import record_change
from services.reserved_categories import get_or_create_adjustment_category
from storage.models.account import Account
from storage.models.transaction import Transaction


async def create_account(db: AsyncSession, user_id: UUID, data: AccountCreate) -> Account:
    account = Account(
        id=data.id,
        user_id=user_id,
        name=data.name,
        type=data.type,
        currency=data.currency,
        institution=data.institution,
        last_four=data.last_four,
        initial_balance_cents=data.initial_balance_cents,
        current_balance_cents=data.initial_balance_cents,
        color=data.color,
        icon=data.icon,
        sort_order=data.sort_order,
        credit_limit_cents=data.credit_limit_cents,
        statement_day=data.statement_day,
        payment_due_day=data.payment_due_day,
        interest_rate=data.interest_rate,
    )
    db.add(account)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("Ya existe una cuenta con ese id.", code="ACCOUNT_ID_TAKEN") from exc

    await record_change(
        db,
        user_id=user_id,
        entity_type="account",
        op="upsert",
        entity=account,
        payload=AccountOut.model_validate(account).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(account)
    return account


async def list_accounts(
    db: AsyncSession, user_id: UUID, *, include_archived: bool = False
) -> list[Account]:
    stmt = select(Account).where(Account.user_id == user_id, Account.deleted_at.is_(None))
    if not include_archived:
        stmt = stmt.where(Account.is_archived.is_(False))
    stmt = stmt.order_by(Account.sort_order, Account.created_at)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_account(db: AsyncSession, user_id: UUID, account_id: UUID) -> Account:
    account = await db.get(Account, account_id)
    if account is None or account.user_id != user_id or account.deleted_at is not None:
        raise NotFoundError("La cuenta no existe.", code="ACCOUNT_NOT_FOUND")
    return account


async def update_account(
    db: AsyncSession, user_id: UUID, account_id: UUID, data: AccountUpdate
) -> Account:
    account = await get_account(db, user_id, account_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(account, field, value)
    account.updated_at = datetime.now(UTC)
    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="account",
        op="upsert",
        entity=account,
        payload=AccountOut.model_validate(account).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(account)
    return account


async def archive_account(
    db: AsyncSession, user_id: UUID, account_id: UUID, *, force: bool = False
) -> None:
    account = await get_account(db, user_id, account_id)
    if account.current_balance_cents != 0 and not force:
        raise ConflictError(
            "La cuenta tiene saldo distinto de cero. Repite con ?force=true si estás seguro.",
            code="ACCOUNT_HAS_BALANCE",
        )
    account.is_archived = True
    account.updated_at = datetime.now(UTC)
    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="account",
        op="upsert",
        entity=account,
        payload=AccountOut.model_validate(account).model_dump(mode="json"),
    )
    await db.commit()


async def recalculate_account(db: AsyncSession, user_id: UUID, account_id: UUID) -> Account:
    account = await get_account(db, user_id, account_id)
    result = await db.execute(
        select(Transaction)
        .where(Transaction.account_id == account.id, Transaction.deleted_at.is_(None))
        .order_by(Transaction.date, Transaction.created_at)
    )
    entries = [
        LedgerEntry(
            kind=txn.kind,  # type: ignore[arg-type]
            amount_cents=txn.amount_cents,
            transfer_direction=txn.transfer_direction,  # type: ignore[arg-type]
        )
        for txn in result.scalars().all()
    ]
    account.current_balance_cents = compute_balance(
        account.initial_balance_cents,
        entries,
        account.type,  # type: ignore[arg-type]
    )
    account.balance_recalculated_at = datetime.now(UTC)
    account.updated_at = datetime.now(UTC)
    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="account",
        op="upsert",
        entity=account,
        payload=AccountOut.model_validate(account).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(account)
    return account


async def adjust_account(
    db: AsyncSession, user_id: UUID, account_id: UUID, data: AccountAdjustRequest
) -> Transaction:
    account = await get_account(db, user_id, account_id)
    diff = data.real_balance_cents - account.current_balance_cents
    try:
        kind, amount = resolve_adjustment(diff, account.type)  # type: ignore[arg-type]
    except ValueError as exc:
        raise ValidationAppError(
            "El saldo real es igual al que ya tiene la cuenta; no hay nada que ajustar.",
            code="NO_ADJUSTMENT_NEEDED",
        ) from exc

    category = await get_or_create_adjustment_category(db, user_id)
    transaction = Transaction(
        id=uuid4(),
        user_id=user_id,
        account_id=account.id,
        category_id=category.id,
        kind=kind,
        amount_cents=amount,
        currency=account.currency,
        date=date.today(),
        notes=data.note,
        is_reconciled=True,
    )
    db.add(transaction)
    account.current_balance_cents = data.real_balance_cents
    account.balance_recalculated_at = datetime.now(UTC)
    account.updated_at = datetime.now(UTC)
    await db.flush()

    await record_change(
        db,
        user_id=user_id,
        entity_type="transaction",
        op="upsert",
        entity=transaction,
        payload={
            "id": str(transaction.id),
            "account_id": str(account.id),
            "kind": kind,
            "amount_cents": amount,
        },
    )
    await record_change(
        db,
        user_id=user_id,
        entity_type="account",
        op="upsert",
        entity=account,
        payload=AccountOut.model_validate(account).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(transaction)
    return transaction


async def reorder_accounts(db: AsyncSession, user_id: UUID, ids: list[UUID]) -> None:
    for position, account_id in enumerate(ids):
        account = await get_account(db, user_id, account_id)
        account.sort_order = position
        account.updated_at = datetime.now(UTC)
    await db.commit()
