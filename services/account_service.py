"""Orquestación de cuentas: DB + `domain/balances.py`. El signo del saldo se
decide en el dominio; este service solo junta filas y llama a `record_change`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ConflictError, NotFoundError, ValidationAppError
from core.timezone import today_in_business_tz
from domain.balances import LedgerEntry, compute_balance, resolve_adjustment
from domain.credit_cycle import compute_current_cycle, compute_cycle_for_statement_month
from domain.dates import add_months_clamped
from schemas.accounts import (
    AccountAdjustRequest,
    AccountCreate,
    AccountOut,
    AccountStatementOut,
    AccountUpdate,
)
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
        minimum_payment_percent=data.minimum_payment_percent,
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


# ---------------------------------------------------------------------------
# GET /accounts/{id}/statement (PLAN-backend.md §8)
#
# Gasto neto del corte: mismo neteo de reembolsos que el resto del proyecto
# (caso de negocio 5) — se duplica el patrón `_spend_or_refund`/
# `_signed_spend` de report_service.py a propósito en vez de importarlo,
# es privado de ese módulo.
_spend_or_refund = or_(Transaction.kind == "expense", Transaction.refund_of_id.is_not(None))
_signed_spend = case(
    (Transaction.refund_of_id.is_not(None), -Transaction.amount_cents),
    else_=Transaction.amount_cents,
)


async def _statement_spend_cents(
    db: AsyncSession, account_id: UUID, period_start: date, period_end: date
) -> int:
    stmt = select(func.coalesce(func.sum(_signed_spend), 0)).where(
        Transaction.account_id == account_id,
        Transaction.deleted_at.is_(None),
        Transaction.date >= period_start,
        Transaction.date <= period_end,
        _spend_or_refund,
    )
    return int((await db.execute(stmt)).scalar_one())


async def _balance_as_of(db: AsyncSession, account: Account, as_of: date) -> int:
    """Reconstruye el saldo de la cuenta a una fecha pasada — igual que
    `recalculate_account`, pero cortando el ledger en `as_of` en vez de
    tomarlo completo (`current_balance_cents` es de HOY, no sirve para un
    corte anterior)."""
    result = await db.execute(
        select(Transaction)
        .where(
            Transaction.account_id == account.id,
            Transaction.deleted_at.is_(None),
            Transaction.date <= as_of,
        )
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
    return compute_balance(account.initial_balance_cents, entries, account.type)  # type: ignore[arg-type]


async def get_statement(
    db: AsyncSession, user_id: UUID, account_id: UUID, cycle: str
) -> AccountStatementOut:
    account = await get_account(db, user_id, account_id)
    if account.type != "credit_card":
        raise ValidationAppError(
            "El estado de cuenta solo aplica a tarjetas de crédito.",
            code="ACCOUNT_NOT_CREDIT_CARD",
        )
    if account.statement_day is None or account.payment_due_day is None:
        raise ValidationAppError(
            "La cuenta no tiene día de corte y de pago configurados.",
            code="ACCOUNT_CYCLE_NOT_CONFIGURED",
        )

    today = today_in_business_tz()
    current = compute_current_cycle(today, account.statement_day, account.payment_due_day)

    if cycle == "current":
        target = current
    elif cycle == "previous":
        anchor = add_months_clamped(current.statement_date.replace(day=1), -1)
        target = compute_cycle_for_statement_month(
            today, anchor.year, anchor.month, account.statement_day, account.payment_due_day
        )
    else:
        try:
            year_str, month_str = cycle.split("-")
            year, month = int(year_str), int(month_str)
        except ValueError as exc:
            raise ValidationAppError(
                "cycle debe ser 'current', 'previous' o 'YYYY-MM'.",
                field="cycle",
                code="INVALID_CYCLE",
            ) from exc
        target = compute_cycle_for_statement_month(
            today, year, month, account.statement_day, account.payment_due_day
        )

    period_start = add_months_clamped(target.statement_date, -1) + timedelta(days=1)
    period_end = target.statement_date

    spend_cents = await _statement_spend_cents(db, account_id, period_start, period_end)
    balance_cents = await _balance_as_of(db, account, target.statement_date)

    minimum_cents = None
    if account.minimum_payment_percent is not None:
        raw = (Decimal(balance_cents) * account.minimum_payment_percent / Decimal(100)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        minimum_cents = max(0, int(raw))

    return AccountStatementOut(
        cycle=cycle,
        period_start=period_start,
        period_end=period_end,
        statement_date=target.statement_date,
        payment_due_date=target.payment_due_date,
        spend_cents=spend_cents,
        balance_cents=balance_cents,
        minimum_cents=minimum_cents,
    )
