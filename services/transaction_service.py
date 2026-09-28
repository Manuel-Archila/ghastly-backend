"""Orquestación de transacciones.

`create_transaction`, `transfer` y `refund` NO hacen commit: son las tres
escrituras que "crean dinero" (CLAUDE.md) y pasan por
`core/idempotency.py::handle_idempotent_write`, que hace el único commit
junto con la fila de `idempotency_keys` — atómico o nada. El resto de
funciones de este módulo sí commitea solo, como cualquier otro service.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from datetime import date as date_
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Text, cast, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import ARRAY as PGArray
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ConflictError, NotFoundError, ValidationAppError
from core.pagination import Cursor, CursorError
from domain.balances import LedgerEntry, signed_delta
from domain.duplicates import DuplicateCandidate, ExistingTransaction, find_possible_duplicate
from domain.money import Money
from schemas.accounts import AccountOut
from schemas.transactions import (
    DuplicateWarning,
    RefundCreate,
    TransactionCreate,
    TransactionCreateResult,
    TransactionListOut,
    TransactionOut,
    TransactionStats,
    TransferCreate,
)
from services import budget_service, transaction_template_service
from services.change_log import record_change
from services.query_filters import exclude_transfers
from storage.models.account import Account
from storage.models.category import Category
from storage.models.debt import DebtPayment
from storage.models.fx import FxRate
from storage.models.goal import GoalContribution
from storage.models.transaction import Transaction


def _account_payload(account: Account) -> dict[str, Any]:
    return AccountOut.model_validate(account).model_dump(mode="json")


async def _check_budget_alert(db: AsyncSession, user_id: UUID, transaction: Transaction) -> None:
    """Gancho "tras cada escritura" (PLAN-backend.md §9): solo un gasto
    categorizado puede empujar una categoría sobre el umbral 80%/100%, así
    que es lo único que dispara la evaluación. Un reembolso RESTA del gasto
    (caso de negocio 5) y nunca cruza un umbral hacia arriba, así que
    `refund()` no llama esto."""
    if transaction.kind != "expense" or transaction.category_id is None:
        return
    month = f"{transaction.date.year:04d}-{transaction.date.month:02d}"
    await budget_service.check_alerts_for_category(db, user_id, transaction.category_id, month)


DUPLICATE_WINDOW_MINUTES = 5


async def _get_owned_account(db: AsyncSession, user_id: UUID, account_id: UUID) -> Account:
    account = await db.get(Account, account_id)
    if account is None or account.user_id != user_id or account.deleted_at is not None:
        raise NotFoundError("La cuenta no existe.", code="ACCOUNT_NOT_FOUND")
    return account


async def _get_owned_category(db: AsyncSession, user_id: UUID, category_id: UUID) -> Category:
    category = await db.get(Category, category_id)
    if category is None or category.user_id != user_id or category.deleted_at is not None:
        raise NotFoundError("La categoría no existe.", code="CATEGORY_NOT_FOUND")
    return category


async def _get_owned_transaction(
    db: AsyncSession, user_id: UUID, transaction_id: UUID, *, include_deleted: bool = False
) -> Transaction:
    transaction = await db.get(Transaction, transaction_id)
    if transaction is None or transaction.user_id != user_id:
        raise NotFoundError("La transacción no existe.", code="TRANSACTION_NOT_FOUND")
    if transaction.deleted_at is not None and not include_deleted:
        raise NotFoundError("La transacción no existe.", code="TRANSACTION_NOT_FOUND")
    return transaction


async def _resolve_fx(
    db: AsyncSession, currency: str, amount_cents: int, on_date: date_, client_rate: Decimal | None
) -> tuple[Decimal | None, int | None]:
    """Caso de negocio 4: la tasa se congela al crear y nunca se recalcula."""
    if currency == "GTQ":
        return None, amount_cents

    rate = client_rate
    if rate is None:
        result = await db.execute(
            select(FxRate.rate).where(
                FxRate.date == on_date,
                FxRate.from_currency == currency,
                FxRate.to_currency == "GTQ",
            )
        )
        rate = result.scalar_one_or_none()
        if rate is None:
            raise ValidationAppError(
                "No hay tasa de cambio cargada para esa fecha; envía fx_rate.",
                field="fx_rate",
                code="FX_RATE_REQUIRED",
            )
    base_amount_cents = Money(amount_cents, currency).convert(rate, "GTQ").cents
    return rate, base_amount_cents


def _apply_ledger_entry(account: Account, entry: LedgerEntry) -> None:
    account.current_balance_cents += signed_delta(entry, account.type)  # type: ignore[arg-type]
    account.updated_at = datetime.now(UTC)
    account.balance_recalculated_at = datetime.now(UTC)


async def _find_duplicate_warning(
    db: AsyncSession, account_id: UUID, amount_cents: int, exclude_id: UUID, occurred_at: datetime
) -> DuplicateWarning | None:
    window = timedelta(minutes=DUPLICATE_WINDOW_MINUTES)
    result = await db.execute(
        select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.amount_cents == amount_cents,
            Transaction.deleted_at.is_(None),
            Transaction.id != exclude_id,
            Transaction.created_at.between(occurred_at - window, occurred_at + window),
        )
    )
    existing = [
        ExistingTransaction(
            id=t.id, account_id=t.account_id, amount_cents=t.amount_cents, occurred_at=t.created_at
        )
        for t in result.scalars().all()
    ]
    candidate = DuplicateCandidate(account_id, amount_cents, occurred_at)
    match = find_possible_duplicate(candidate, existing, window_minutes=DUPLICATE_WINDOW_MINUTES)
    return DuplicateWarning(transaction_id=match.id) if match else None


async def create_transaction(
    db: AsyncSession, user_id: UUID, data: TransactionCreate
) -> TransactionCreateResult:
    account = await _get_owned_account(db, user_id, data.account_id)
    if account.is_archived:
        raise ValidationAppError("La cuenta está archivada.", code="ACCOUNT_ARCHIVED")

    if data.category_id is not None:
        category = await _get_owned_category(db, user_id, data.category_id)
        if category.kind != data.kind:
            raise ValidationAppError(
                "La categoría no coincide con el tipo de movimiento (ingreso/gasto).",
                field="category_id",
                code="CATEGORY_KIND_MISMATCH",
            )

    fx_rate, base_amount_cents = await _resolve_fx(
        db, data.currency, data.amount_cents, data.date, data.fx_rate
    )

    now = datetime.now(UTC)
    transaction = Transaction(
        id=data.id,
        user_id=user_id,
        account_id=account.id,
        category_id=data.category_id,
        kind=data.kind,
        amount_cents=data.amount_cents,
        currency=data.currency,
        fx_rate=fx_rate,
        base_amount_cents=base_amount_cents,
        date=data.date,
        description=data.description,
        merchant=data.merchant,
        notes=data.notes,
        is_tax_relevant=data.is_tax_relevant,
        is_extraordinary=data.is_extraordinary,
        tags=data.tags,
        created_at=now,
        updated_at=now,
    )
    db.add(transaction)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            "Ya existe una transacción con ese id.", code="TRANSACTION_ID_TAKEN"
        ) from exc

    _apply_ledger_entry(account, LedgerEntry(kind=data.kind, amount_cents=data.amount_cents))

    warning = await _find_duplicate_warning(db, account.id, data.amount_cents, transaction.id, now)

    await record_change(
        db,
        user_id=user_id,
        entity_type="transaction",
        op="upsert",
        entity=transaction,
        payload=TransactionOut.model_validate(transaction).model_dump(mode="json"),
    )
    await record_change(
        db,
        user_id=user_id,
        entity_type="account",
        op="upsert",
        entity=account,
        payload=_account_payload(account),
    )

    await _check_budget_alert(db, user_id, transaction)

    if data.template_id is not None:
        await transaction_template_service.mark_used(db, user_id, data.template_id)

    return TransactionCreateResult(
        transaction=TransactionOut.model_validate(transaction), warning=warning
    )


async def transfer(
    db: AsyncSession, user_id: UUID, data: TransferCreate
) -> tuple[Transaction, Transaction]:
    if data.from_account_id == data.to_account_id:
        raise ValidationAppError(
            "La cuenta origen y destino no pueden ser la misma.", code="TRANSFER_SAME_ACCOUNT"
        )
    from_account = await _get_owned_account(db, user_id, data.from_account_id)
    to_account = await _get_owned_account(db, user_id, data.to_account_id)
    for account in (from_account, to_account):
        if account.is_archived:
            raise ValidationAppError("La cuenta está archivada.", code="ACCOUNT_ARCHIVED")

    transfer_group_id = uuid4()
    now = datetime.now(UTC)

    out_txn = Transaction(
        id=data.out_transaction_id,
        user_id=user_id,
        account_id=from_account.id,
        kind="transfer",
        amount_cents=data.amount_cents,
        currency=from_account.currency,
        date=data.date,
        description=data.description,
        notes=data.notes,
        transfer_group_id=transfer_group_id,
        transfer_direction="out",
        created_at=now,
        updated_at=now,
    )
    in_txn = Transaction(
        id=data.in_transaction_id,
        user_id=user_id,
        account_id=to_account.id,
        kind="transfer",
        amount_cents=data.amount_cents,
        currency=to_account.currency,
        date=data.date,
        description=data.description,
        notes=data.notes,
        transfer_group_id=transfer_group_id,
        transfer_direction="in",
        created_at=now,
        updated_at=now,
    )
    db.add_all([out_txn, in_txn])
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            "Ya existe una transacción con ese id.", code="TRANSACTION_ID_TAKEN"
        ) from exc

    _apply_ledger_entry(
        from_account,
        LedgerEntry(kind="transfer", amount_cents=data.amount_cents, transfer_direction="out"),
    )
    _apply_ledger_entry(
        to_account,
        LedgerEntry(kind="transfer", amount_cents=data.amount_cents, transfer_direction="in"),
    )

    for txn in (out_txn, in_txn):
        await record_change(
            db,
            user_id=user_id,
            entity_type="transaction",
            op="upsert",
            entity=txn,
            payload=TransactionOut.model_validate(txn).model_dump(mode="json"),
        )
    for account in (from_account, to_account):
        await record_change(
            db,
            user_id=user_id,
            entity_type="account",
            op="upsert",
            entity=account,
            payload=_account_payload(account),
        )

    return out_txn, in_txn


async def refund(
    db: AsyncSession, user_id: UUID, original_id: UUID, data: RefundCreate
) -> Transaction:
    original = await _get_owned_transaction(db, user_id, original_id)
    if original.kind != "expense":
        raise ValidationAppError(
            "Solo se puede reembolsar un gasto.", code="REFUND_REQUIRES_EXPENSE"
        )
    amount = data.amount_cents or original.amount_cents
    account = await _get_owned_account(db, user_id, original.account_id)
    refund_date = data.date or date_.today()
    fx_rate, base_amount_cents = await _resolve_fx(
        db, original.currency, amount, refund_date, data.fx_rate
    )

    now = datetime.now(UTC)
    refund_txn = Transaction(
        id=data.id,
        user_id=user_id,
        account_id=account.id,
        category_id=original.category_id,
        kind="income",
        amount_cents=amount,
        currency=original.currency,
        fx_rate=fx_rate,
        base_amount_cents=base_amount_cents,
        date=refund_date,
        description=f"Reembolso: {original.description}" if original.description else "Reembolso",
        notes=data.notes,
        refund_of_id=original.id,
        created_at=now,
        updated_at=now,
    )
    db.add(refund_txn)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            "Ya existe una transacción con ese id.", code="TRANSACTION_ID_TAKEN"
        ) from exc

    _apply_ledger_entry(account, LedgerEntry(kind="income", amount_cents=amount))

    await record_change(
        db,
        user_id=user_id,
        entity_type="transaction",
        op="upsert",
        entity=refund_txn,
        payload=TransactionOut.model_validate(refund_txn).model_dump(mode="json"),
    )
    await record_change(
        db,
        user_id=user_id,
        entity_type="account",
        op="upsert",
        entity=account,
        payload=_account_payload(account),
    )
    return refund_txn


async def list_transactions(
    db: AsyncSession,
    user_id: UUID,
    *,
    account_id: UUID | None = None,
    category_id: UUID | None = None,
    kind: str | None = None,
    q: str | None = None,
    tags: list[str] | None = None,
    date_from: date_ | None = None,
    date_to: date_ | None = None,
    min_cents: int | None = None,
    max_cents: int | None = None,
    is_reconciled: bool | None = None,
    include_deleted: bool = False,
    cursor: str | None = None,
    limit: int = 50,
) -> TransactionListOut:
    stmt = select(Transaction).where(Transaction.user_id == user_id)
    if not include_deleted:
        stmt = stmt.where(Transaction.deleted_at.is_(None))
    if account_id is not None:
        stmt = stmt.where(Transaction.account_id == account_id)
    if category_id is not None:
        stmt = stmt.where(Transaction.category_id == category_id)
    if kind is not None:
        stmt = stmt.where(Transaction.kind == kind)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                Transaction.description.ilike(pattern),
                Transaction.merchant.ilike(pattern),
                Transaction.notes.ilike(pattern),
            )
        )
    if tags:
        # Cualquiera de los tags pedidos (operador de overlap de Postgres,
        # no está en el comparator genérico de ARRAY de SQLAlchemy). Cast
        # explícito: la columna es TEXT[], el bind param sale VARCHAR[] por
        # default y Postgres no los compara sin castear.
        stmt = stmt.where(Transaction.tags.op("&&")(cast(tags, PGArray(Text))))
    if date_from is not None:
        stmt = stmt.where(Transaction.date >= date_from)
    if date_to is not None:
        stmt = stmt.where(Transaction.date <= date_to)
    if min_cents is not None:
        stmt = stmt.where(Transaction.amount_cents >= min_cents)
    if max_cents is not None:
        stmt = stmt.where(Transaction.amount_cents <= max_cents)
    if is_reconciled is not None:
        stmt = stmt.where(Transaction.is_reconciled == is_reconciled)

    if cursor is not None:
        try:
            decoded = Cursor.decode(cursor)
        except CursorError as exc:
            raise ValidationAppError("El cursor no es válido.", code="INVALID_CURSOR") from exc
        stmt = stmt.where(tuple_(Transaction.date, Transaction.id) < (decoded.date, decoded.id))

    stmt = stmt.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(limit + 1)
    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = Cursor(date=last.date, id=last.id).encode()

    return TransactionListOut(
        items=[TransactionOut.model_validate(t) for t in rows], next_cursor=next_cursor
    )


async def get_transaction(db: AsyncSession, user_id: UUID, transaction_id: UUID) -> Transaction:
    return await _get_owned_transaction(db, user_id, transaction_id)


async def _locked_amount_reason(db: AsyncSession, transaction: Transaction) -> str | None:
    """Por qué NO se puede editar el monto de esta transacción, o `None` si sí
    se puede. Cada uno de estos casos tiene un monto que otra fila asume que
    coincide con `amount_cents` — cambiarlo acá los desincroniza:
    - `transfer`: son dos filas que deben coincidir; se edita desde las dos
      cuentas, no desde una transacción suelta.
    - cuota de un plan (`installment_id`): el monto lo define el cronograma
      del plan (`installments.amount_cents`).
    - liquidación de un "me deben" (`receivable_id`): el monto ya quedó fijo
      en `receivables.amount_cents` al liquidar.
    - pago de deuda o aporte a meta: `debt_payments`/`goal_contributions`
      guardan su propio monto aparte, usado para la deuda/meta pendiente.
    Un reembolso (`refund_of_id`) y una confirmación de recurrente
    (`recurring_rule_id`) SÍ se pueden editar: no hay ninguna otra fila cuyo
    monto dependa del suyo.

    Límite conocido: `debt_payments.transaction_id` guarda una sola fila
    (prioriza la de interés cuando existen las dos, interés y capital como
    transacciones separadas — `debt_service.record_payment`); la pata de
    capital en ese caso puntual no queda protegida acá."""
    if transaction.kind == "transfer":
        return "TRANSFER_AMOUNT_EDIT_UNSUPPORTED"
    if transaction.installment_id is not None:
        return "INSTALLMENT_AMOUNT_LOCKED"
    if transaction.receivable_id is not None:
        return "RECEIVABLE_AMOUNT_LOCKED"

    debt_linked = await db.execute(
        select(DebtPayment.id).where(DebtPayment.transaction_id == transaction.id)
    )
    if debt_linked.first() is not None:
        return "DEBT_PAYMENT_AMOUNT_LOCKED"

    goal_linked = await db.execute(
        select(GoalContribution.id).where(GoalContribution.transaction_id == transaction.id)
    )
    if goal_linked.first() is not None:
        return "GOAL_CONTRIBUTION_AMOUNT_LOCKED"

    return None


async def update_transaction(
    db: AsyncSession, user_id: UUID, transaction_id: UUID, updates: dict[str, Any]
) -> Transaction:
    transaction = await _get_owned_transaction(db, user_id, transaction_id)
    if "category_id" in updates and updates["category_id"] is not None:
        category = await _get_owned_category(db, user_id, updates["category_id"])
        if category.kind != transaction.kind:
            raise ValidationAppError(
                "La categoría no coincide con el tipo de movimiento (ingreso/gasto).",
                field="category_id",
                code="CATEGORY_KIND_MISMATCH",
            )

    account: Account | None = None
    new_amount = updates.get("amount_cents")
    if new_amount is not None and new_amount != transaction.amount_cents:
        reason = await _locked_amount_reason(db, transaction)
        if reason is not None:
            raise ValidationAppError(
                "El monto de esta transacción no se puede editar directamente.",
                field="amount_cents",
                code=reason,
            )
        account = await _get_owned_account(db, user_id, transaction.account_id)
        old_delta = signed_delta(
            LedgerEntry(kind=transaction.kind, amount_cents=transaction.amount_cents),  # type: ignore[arg-type]
            account.type,  # type: ignore[arg-type]
        )
        new_delta = signed_delta(
            LedgerEntry(kind=transaction.kind, amount_cents=new_amount),  # type: ignore[arg-type]
            account.type,  # type: ignore[arg-type]
        )
        account.current_balance_cents += new_delta - old_delta
        account.updated_at = datetime.now(UTC)
        # El monto en USD se congela (regla de negocio 4): se recalcula
        # `base_amount_cents` con la MISMA fx_rate ya guardada, nunca con una
        # tasa nueva — GTQ no tiene fx_rate y base_amount_cents == amount_cents.
        if transaction.fx_rate is not None:
            updates["base_amount_cents"] = (
                Money(new_amount, transaction.currency).convert(transaction.fx_rate, "GTQ").cents
            )
        else:
            updates["base_amount_cents"] = new_amount

    for field, value in updates.items():
        setattr(transaction, field, value)
    transaction.updated_at = datetime.now(UTC)

    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="transaction",
        op="upsert",
        entity=transaction,
        payload=TransactionOut.model_validate(transaction).model_dump(mode="json"),
    )
    if account is not None:
        await record_change(
            db,
            user_id=user_id,
            entity_type="account",
            op="upsert",
            entity=account,
            payload=_account_payload(account),
        )
    await _check_budget_alert(db, user_id, transaction)
    await db.commit()
    await db.refresh(transaction)
    return transaction


async def delete_transaction(db: AsyncSession, user_id: UUID, transaction_id: UUID) -> None:
    """Borrado lógico. NO revierte el saldo (igual que un `DELETE` de cualquier
    fila con historia contable): para corregir el saldo se usa `/recalculate`."""
    transaction = await _get_owned_transaction(db, user_id, transaction_id)
    transaction.deleted_at = datetime.now(UTC)
    transaction.updated_at = datetime.now(UTC)

    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="transaction",
        op="delete",
        entity=transaction,
        payload={"id": str(transaction.id)},
    )
    await db.commit()


async def restore_transaction(db: AsyncSession, user_id: UUID, transaction_id: UUID) -> Transaction:
    transaction = await _get_owned_transaction(db, user_id, transaction_id, include_deleted=True)
    transaction.deleted_at = None
    transaction.updated_at = datetime.now(UTC)

    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="transaction",
        op="upsert",
        entity=transaction,
        payload=TransactionOut.model_validate(transaction).model_dump(mode="json"),
    )
    await _check_budget_alert(db, user_id, transaction)
    await db.commit()
    await db.refresh(transaction)
    return transaction


async def bulk_categorize(
    db: AsyncSession, user_id: UUID, ids: list[UUID], category_id: UUID
) -> int:
    await _get_owned_category(db, user_id, category_id)
    result = await db.execute(
        update(Transaction)
        .where(
            Transaction.user_id == user_id,
            Transaction.id.in_(ids),
            Transaction.deleted_at.is_(None),
        )
        .values(category_id=category_id, updated_at=datetime.now(UTC))
    )
    await db.commit()
    return result.rowcount or 0  # type: ignore[attr-defined]  # CursorResult sí lo tiene


async def list_duplicates(
    db: AsyncSession, user_id: UUID, *, window_minutes: int = 5
) -> list[Transaction]:
    """Transacciones que tienen al menos otra igual (misma cuenta+monto) cerca
    en el tiempo. Devuelve las filas marcadas, no pares — el cliente las agrupa."""
    result = await db.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id, Transaction.deleted_at.is_(None))
        .order_by(Transaction.account_id, Transaction.created_at)
    )
    rows = list(result.scalars().all())
    flagged_ids: set[UUID] = set()
    for row in rows:
        candidate = DuplicateCandidate(row.account_id, row.amount_cents, row.created_at)
        others = [
            ExistingTransaction(
                id=r.id,
                account_id=r.account_id,
                amount_cents=r.amount_cents,
                occurred_at=r.created_at,
            )
            for r in rows
            if r.id != row.id
        ]
        if find_possible_duplicate(candidate, others, window_minutes=window_minutes) is not None:
            flagged_ids.add(row.id)
    return [r for r in rows if r.id in flagged_ids]


async def get_stats(
    db: AsyncSession,
    user_id: UUID,
    *,
    account_id: UUID | None = None,
    category_id: UUID | None = None,
    date_from: date_ | None = None,
    date_to: date_ | None = None,
    in_base_currency: bool = False,
) -> TransactionStats:
    """`in_base_currency=True` suma el equivalente en GTQ (caso 4) en vez del
    monto crudo — correcto para rollups que mezclan cuentas de distinta
    moneda (dashboard, `/reports/comparison`). El default (`False`) es lo
    que espera `GET /transactions/stats`: si filtrás por una cuenta en USD,
    querés ver sus totales en USD, no convertidos."""
    stmt = select(Transaction).where(
        Transaction.user_id == user_id, Transaction.deleted_at.is_(None)
    )
    stmt = exclude_transfers(stmt)  # regla de negocio 1
    if account_id is not None:
        stmt = stmt.where(Transaction.account_id == account_id)
    if category_id is not None:
        stmt = stmt.where(Transaction.category_id == category_id)
    if date_from is not None:
        stmt = stmt.where(Transaction.date >= date_from)
    if date_to is not None:
        stmt = stmt.where(Transaction.date <= date_to)

    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    def _amount(r: Transaction) -> int:
        if in_base_currency and r.base_amount_cents is not None:
            return r.base_amount_cents
        return r.amount_cents

    total_income = sum(_amount(r) for r in rows if r.kind == "income")
    total_expense = sum(_amount(r) for r in rows if r.kind == "expense")
    return TransactionStats(
        count=len(rows),
        total_income_cents=total_income,
        total_expense_cents=total_expense,
        net_cents=total_income - total_expense,
    )
