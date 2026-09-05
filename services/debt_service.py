"""Orquestación de deudas (préstamos, tarjetas ya modeladas fuera de `accounts`).

`record_payment` separa capital/interés (PLAN-backend §6): el interés es
gasto real (afecta presupuesto), el capital reduce el pasivo. Si la deuda
tiene `linked_account_id` (una cuenta `type=loan` que la espeja), el
capital se mueve ahí como una transferencia normal — nuestro
`domain/balances.py` ya sabe que un pago a una cuenta pasiva reduce lo que
se debe. Si no hay cuenta enlazada, el capital se registra como gasto en
la categoría reservada "Deudas e intereses" (no hay dónde más reflejar la
salida de efectivo). Como crea dinero, no commitea sola.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ConflictError, NotFoundError, ValidationAppError
from domain.amortization import AmortizationError, amortize
from domain.balances import LedgerEntry, signed_delta
from schemas.debts import AmortizationRow, DebtCreate, DebtOut, DebtPaymentCreate, DebtUpdate
from schemas.transactions import TransferCreate
from services import transaction_service
from services.change_log import record_change
from services.reserved_categories import get_or_create_debt_category
from storage.models.account import Account
from storage.models.debt import Debt, DebtPayment
from storage.models.transaction import Transaction


async def _get_owned(db: AsyncSession, user_id: UUID, debt_id: UUID) -> Debt:
    debt = await db.get(Debt, debt_id)
    if debt is None or debt.user_id != user_id or debt.deleted_at is not None:
        raise NotFoundError("La deuda no existe.", code="DEBT_NOT_FOUND")
    return debt


async def create_debt(db: AsyncSession, user_id: UUID, data: DebtCreate) -> Debt:
    if data.linked_account_id is not None:
        account = await db.get(Account, data.linked_account_id)
        if account is None or account.user_id != user_id or account.deleted_at is not None:
            raise NotFoundError("La cuenta enlazada no existe.", code="ACCOUNT_NOT_FOUND")

    debt = Debt(
        id=data.id,
        user_id=user_id,
        name=data.name,
        type=data.type,
        principal_cents=data.principal_cents,
        balance_cents=data.principal_cents,
        monthly_interest_rate=data.monthly_interest_rate,
        monthly_payment_cents=data.monthly_payment_cents,
        start_date=data.start_date,
        term_months=data.term_months,
        linked_account_id=data.linked_account_id,
    )
    db.add(debt)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("Ya existe una deuda con ese id.", code="DEBT_ID_TAKEN") from exc

    await record_change(
        db,
        user_id=user_id,
        entity_type="debt",
        op="upsert",
        entity=debt,
        payload=DebtOut.model_validate(debt).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(debt)
    return debt


async def list_debts(db: AsyncSession, user_id: UUID) -> list[Debt]:
    result = await db.execute(
        select(Debt).where(Debt.user_id == user_id, Debt.deleted_at.is_(None))
    )
    return list(result.scalars().all())


async def get_debt(db: AsyncSession, user_id: UUID, debt_id: UUID) -> Debt:
    return await _get_owned(db, user_id, debt_id)


async def update_debt(db: AsyncSession, user_id: UUID, debt_id: UUID, data: DebtUpdate) -> Debt:
    debt = await _get_owned(db, user_id, debt_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(debt, field, value)
    debt.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(debt)
    return debt


async def delete_debt(db: AsyncSession, user_id: UUID, debt_id: UUID) -> None:
    debt = await _get_owned(db, user_id, debt_id)
    debt.deleted_at = datetime.now(UTC)
    debt.updated_at = datetime.now(UTC)
    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="debt",
        op="delete",
        entity=debt,
        payload={"id": str(debt.id)},
    )
    await db.commit()


async def get_amortization(db: AsyncSession, user_id: UUID, debt_id: UUID) -> list[AmortizationRow]:
    debt = await _get_owned(db, user_id, debt_id)
    if debt.term_months is None:
        raise ValidationAppError(
            "Esta deuda no tiene plazo (term_months) definido.", code="DEBT_MISSING_TERM"
        )
    try:
        entries = amortize(debt.principal_cents, debt.monthly_interest_rate, debt.term_months)
    except AmortizationError as exc:
        raise ValidationAppError(str(exc), code="DEBT_AMORTIZATION_ERROR") from exc
    return [
        AmortizationRow(
            number=e.number,
            payment_cents=e.payment_cents,
            principal_cents=e.principal_cents,
            interest_cents=e.interest_cents,
            remaining_balance_cents=e.remaining_balance_cents,
        )
        for e in entries
    ]


def _round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


async def record_payment(
    db: AsyncSession, user_id: UUID, debt_id: UUID, data: DebtPaymentCreate
) -> DebtPayment:
    debt = await _get_owned(db, user_id, debt_id)
    from_account = await db.get(Account, data.from_account_id)
    if (
        from_account is None
        or from_account.user_id != user_id
        or from_account.deleted_at is not None
    ):
        raise NotFoundError("La cuenta no existe.", code="ACCOUNT_NOT_FOUND")

    if data.principal_cents is not None and data.interest_cents is not None:
        principal, interest = data.principal_cents, data.interest_cents
    else:
        interest = _round_half_up(Decimal(debt.balance_cents) * debt.monthly_interest_rate)
        principal = data.total_cents - interest - data.fees_cents
        if principal < 0:
            raise ValidationAppError(
                "total_cents no alcanza para cubrir el interés y las comisiones.",
                code="DEBT_PAYMENT_TOO_SMALL",
            )

    now = datetime.now(UTC)
    linked_transaction_id: UUID | None = None
    interest_and_fees = interest + data.fees_cents

    if interest_and_fees > 0:
        category = await get_or_create_debt_category(db, user_id)
        interest_txn = Transaction(
            id=data.interest_transaction_id,
            user_id=user_id,
            account_id=from_account.id,
            category_id=category.id,
            kind="expense",
            amount_cents=interest_and_fees,
            currency=from_account.currency,
            date=data.date,
            description=f"Interés y comisiones — {debt.name}",
            created_at=now,
            updated_at=now,
        )
        db.add(interest_txn)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                "Ya existe una transacción con ese id.", code="TRANSACTION_ID_TAKEN"
            ) from exc
        from_account.current_balance_cents += signed_delta(
            LedgerEntry(kind="expense", amount_cents=interest_and_fees),
            from_account.type,  # type: ignore[arg-type]
        )
        from_account.updated_at = now
        linked_transaction_id = interest_txn.id
        await record_change(
            db,
            user_id=user_id,
            entity_type="transaction",
            op="upsert",
            entity=interest_txn,
            payload={"id": str(interest_txn.id), "debt_id": str(debt.id)},
        )

    if principal > 0:
        if debt.linked_account_id is not None:
            await transaction_service.transfer(
                db,
                user_id,
                TransferCreate(
                    out_transaction_id=data.principal_transfer_out_id,
                    in_transaction_id=data.principal_transfer_in_id,
                    from_account_id=from_account.id,
                    to_account_id=debt.linked_account_id,
                    amount_cents=principal,
                    date=data.date,
                    description=f"Pago a {debt.name}",
                ),
            )
        else:
            category = await get_or_create_debt_category(db, user_id)
            principal_txn = Transaction(
                id=data.principal_transaction_id,
                user_id=user_id,
                account_id=from_account.id,
                category_id=category.id,
                kind="expense",
                amount_cents=principal,
                currency=from_account.currency,
                date=data.date,
                description=f"Abono a capital — {debt.name}",
                created_at=now,
                updated_at=now,
            )
            db.add(principal_txn)
            try:
                await db.flush()
            except IntegrityError as exc:
                await db.rollback()
                raise ConflictError(
                    "Ya existe una transacción con ese id.", code="TRANSACTION_ID_TAKEN"
                ) from exc
            from_account.current_balance_cents += signed_delta(
                LedgerEntry(kind="expense", amount_cents=principal),
                from_account.type,  # type: ignore[arg-type]
            )
            from_account.updated_at = now
            if linked_transaction_id is None:
                linked_transaction_id = principal_txn.id
            await record_change(
                db,
                user_id=user_id,
                entity_type="transaction",
                op="upsert",
                entity=principal_txn,
                payload={"id": str(principal_txn.id), "debt_id": str(debt.id)},
            )

    debt.balance_cents -= principal
    debt.updated_at = now
    if debt.balance_cents <= 0:
        debt.balance_cents = 0
        debt.status = "paid_off"

    payment = DebtPayment(
        id=data.id,
        user_id=user_id,
        debt_id=debt.id,
        date=data.date,
        total_cents=data.total_cents,
        principal_cents=principal,
        interest_cents=interest,
        fees_cents=data.fees_cents,
        transaction_id=linked_transaction_id,
    )
    db.add(payment)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("Ya existe un pago con ese id.", code="DEBT_PAYMENT_ID_TAKEN") from exc
    await record_change(
        db,
        user_id=user_id,
        entity_type="debt",
        op="upsert",
        entity=debt,
        payload=DebtOut.model_validate(debt).model_dump(mode="json"),
    )
    return payment
