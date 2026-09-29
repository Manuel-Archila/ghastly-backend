"""Orquestación de reglas recurrentes (suscripciones, alquiler, seguros).

`confirm_rule` es la única función que mueve dinero (crea la transacción
cuando `auto_create=false`) y por eso no commitea sola — pasa por
`core/idempotency.py::handle_idempotent_write` igual que `transactions`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from datetime import date as date_
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ConflictError, NotFoundError, ValidationAppError
from domain.balances import LedgerEntry, signed_delta
from domain.money import Money
from domain.recurrence import detect_price_increase, monthly_equivalent_cents, next_occurrence
from schemas.recurring import (
    RecurringConfirmRequest,
    RecurringRuleCreate,
    RecurringRuleOut,
    RecurringRuleUpdate,
    SubscriptionsSummaryOut,
    SubscriptionSummaryItem,
)
from services.category_requirement import ensure_category_present
from services.change_log import record_change
from storage.models.account import Account
from storage.models.recurring import RecurringRule
from storage.models.transaction import Transaction

CANCEL_CANDIDATE_WINDOW_DAYS = 60


async def _get_owned(db: AsyncSession, user_id: UUID, rule_id: UUID) -> RecurringRule:
    rule = await db.get(RecurringRule, rule_id)
    if rule is None or rule.user_id != user_id or rule.deleted_at is not None:
        raise NotFoundError("La regla recurrente no existe.", code="RECURRING_RULE_NOT_FOUND")
    return rule


async def create_rule(db: AsyncSession, user_id: UUID, data: RecurringRuleCreate) -> RecurringRule:
    account = await db.get(Account, data.account_id)
    if account is None or account.user_id != user_id or account.deleted_at is not None:
        raise NotFoundError("La cuenta no existe.", code="ACCOUNT_NOT_FOUND")

    ensure_category_present(data.kind, data.category_id)

    if data.currency != "GTQ" and data.fx_rate is None:
        raise ValidationAppError(
            "Una regla en moneda distinta de GTQ necesita fx_rate.",
            field="fx_rate",
            code="FX_RATE_REQUIRED",
        )

    rule = RecurringRule(
        id=data.id,
        user_id=user_id,
        account_id=data.account_id,
        category_id=data.category_id,
        kind=data.kind,
        name=data.name,
        amount_cents=data.amount_cents,
        currency=data.currency,
        fx_rate=data.fx_rate if data.currency != "GTQ" else None,
        frequency=data.frequency,
        interval=data.interval,
        next_due_date=data.next_due_date,
        end_date=data.end_date,
        auto_create=data.auto_create,
        reminder_days_before=data.reminder_days_before,
        is_extraordinary=data.is_extraordinary,
    )
    db.add(rule)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            "Ya existe una regla recurrente con ese id.", code="RECURRING_RULE_ID_TAKEN"
        ) from exc

    await record_change(
        db,
        user_id=user_id,
        entity_type="recurring_rule",
        op="upsert",
        entity=rule,
        payload=RecurringRuleOut.model_validate(rule).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(rule)
    return rule


async def list_rules(db: AsyncSession, user_id: UUID) -> list[RecurringRule]:
    result = await db.execute(
        select(RecurringRule).where(
            RecurringRule.user_id == user_id, RecurringRule.deleted_at.is_(None)
        )
    )
    return list(result.scalars().all())


async def get_rule(db: AsyncSession, user_id: UUID, rule_id: UUID) -> RecurringRule:
    return await _get_owned(db, user_id, rule_id)


async def update_rule(
    db: AsyncSession, user_id: UUID, rule_id: UUID, data: RecurringRuleUpdate
) -> RecurringRule:
    rule = await _get_owned(db, user_id, rule_id)
    changes = data.model_dump(exclude_unset=True)
    if "category_id" in changes:
        ensure_category_present(rule.kind, changes["category_id"])
    for field, value in changes.items():
        setattr(rule, field, value)
    rule.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(rule)
    return rule


async def delete_rule(db: AsyncSession, user_id: UUID, rule_id: UUID) -> None:
    rule = await _get_owned(db, user_id, rule_id)
    rule.status = "ended"
    rule.deleted_at = datetime.now(UTC)
    rule.updated_at = datetime.now(UTC)
    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="recurring_rule",
        op="delete",
        entity=rule,
        payload={"id": str(rule.id)},
    )
    await db.commit()


async def pause_rule(db: AsyncSession, user_id: UUID, rule_id: UUID) -> RecurringRule:
    rule = await _get_owned(db, user_id, rule_id)
    rule.status = "paused"
    rule.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(rule)
    return rule


async def resume_rule(db: AsyncSession, user_id: UUID, rule_id: UUID) -> RecurringRule:
    rule = await _get_owned(db, user_id, rule_id)
    rule.status = "active"
    rule.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(rule)
    return rule


async def skip_next(db: AsyncSession, user_id: UUID, rule_id: UUID) -> RecurringRule:
    rule = await _get_owned(db, user_id, rule_id)
    rule.next_due_date = next_occurrence(rule.next_due_date, rule.frequency, rule.interval)  # type: ignore[arg-type]
    rule.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(rule)
    return rule


async def confirm_rule(
    db: AsyncSession, user_id: UUID, rule_id: UUID, data: RecurringConfirmRequest
) -> Transaction:
    rule = await _get_owned(db, user_id, rule_id)
    if rule.status != "active":
        raise ConflictError("Esa regla no está activa.", code="RECURRING_RULE_NOT_ACTIVE")
    # Este camino crea la transacción sin pasar por `create_transaction`.
    ensure_category_present(rule.kind, rule.category_id)

    account = await db.get(Account, rule.account_id)
    if account is None:
        raise NotFoundError("La cuenta no existe.", code="ACCOUNT_NOT_FOUND")

    # Caso 4: la tasa ya se congeló al crear la regla (`create_rule`) — acá
    # solo se convierte con esa tasa fija, nunca se recalcula ni se busca
    # una más nueva (no hay de dónde, y tampoco correspondería).
    base_amount_cents = (
        Money(rule.amount_cents, rule.currency).convert(rule.fx_rate, "GTQ").cents  # type: ignore[arg-type]
        if rule.currency != "GTQ"
        else None
    )

    now = datetime.now(UTC)
    transaction = Transaction(
        id=data.id,
        user_id=user_id,
        account_id=rule.account_id,
        category_id=rule.category_id,
        kind=rule.kind,
        amount_cents=rule.amount_cents,
        currency=rule.currency,
        fx_rate=rule.fx_rate,
        base_amount_cents=base_amount_cents,
        date=data.date or date_.today(),
        description=rule.name,
        recurring_rule_id=rule.id,
        is_extraordinary=rule.is_extraordinary,
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

    account.current_balance_cents += signed_delta(
        LedgerEntry(kind=rule.kind, amount_cents=rule.amount_cents),  # type: ignore[arg-type]
        account.type,  # type: ignore[arg-type]
    )
    account.updated_at = now
    account.balance_recalculated_at = now

    if rule.last_amount_cents is not None and detect_price_increase(
        rule.last_amount_cents, rule.amount_cents
    ):
        rule.price_history = [
            *rule.price_history,
            {"date": (data.date or date_.today()).isoformat(), "amount_cents": rule.amount_cents},
        ]
    rule.last_amount_cents = rule.amount_cents
    rule.last_generated_at = now
    rule.next_due_date = next_occurrence(rule.next_due_date, rule.frequency, rule.interval)  # type: ignore[arg-type]
    rule.updated_at = now
    if rule.end_date is not None and rule.next_due_date > rule.end_date:
        rule.status = "ended"

    await record_change(
        db,
        user_id=user_id,
        entity_type="transaction",
        op="upsert",
        entity=transaction,
        payload={"id": str(transaction.id), "recurring_rule_id": str(rule.id)},
    )
    await record_change(
        db,
        user_id=user_id,
        entity_type="recurring_rule",
        op="upsert",
        entity=rule,
        payload=RecurringRuleOut.model_validate(rule).model_dump(mode="json"),
    )
    return transaction


async def list_upcoming(db: AsyncSession, user_id: UUID, days: int = 30) -> list[RecurringRule]:
    today = date_.today()
    result = await db.execute(
        select(RecurringRule)
        .where(
            RecurringRule.user_id == user_id,
            RecurringRule.deleted_at.is_(None),
            RecurringRule.status == "active",
            RecurringRule.next_due_date >= today,
            RecurringRule.next_due_date <= today + timedelta(days=days),
        )
        .order_by(RecurringRule.next_due_date)
    )
    return list(result.scalars().all())


async def subscriptions_summary(db: AsyncSession, user_id: UUID) -> SubscriptionsSummaryOut:
    result = await db.execute(
        select(RecurringRule).where(
            RecurringRule.user_id == user_id,
            RecurringRule.deleted_at.is_(None),
            RecurringRule.status == "active",
            RecurringRule.kind == "expense",
        )
    )
    rules = list(result.scalars().all())

    cutoff = date_.today() - timedelta(days=CANCEL_CANDIDATE_WINDOW_DAYS)
    items: list[SubscriptionSummaryItem] = []
    total_monthly = 0
    for rule in rules:
        monthly = monthly_equivalent_cents(rule.amount_cents, rule.frequency, rule.interval)  # type: ignore[arg-type]
        total_monthly += monthly

        cancel_candidate = False
        if rule.last_generated_at is not None:
            recent = await db.execute(
                select(Transaction.id)
                .where(
                    Transaction.recurring_rule_id == rule.id,
                    Transaction.deleted_at.is_(None),
                    Transaction.date >= cutoff,
                )
                .limit(1)
            )
            cancel_candidate = recent.first() is None

        items.append(
            SubscriptionSummaryItem(
                rule_id=rule.id,
                name=rule.name,
                amount_cents=rule.amount_cents,
                monthly_equivalent_cents=monthly,
                frequency=rule.frequency,  # type: ignore[arg-type]
                next_due_date=rule.next_due_date,
                # `last_amount_cents` se normaliza a `amount_cents` en cada confirm
                # (ver `confirm_rule`), así que compararlos acá casi nunca detecta
                # nada — la señal real es que `price_history` tenga algo anotado.
                price_increased=bool(rule.price_history),
                cancel_candidate=cancel_candidate,
            )
        )

    return SubscriptionsSummaryOut(
        total_monthly_cents=total_monthly,
        total_annualized_cents=total_monthly * 12,
        items=items,
    )
