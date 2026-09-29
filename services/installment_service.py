"""Orquestación de cuotas (caso de negocio 6).

`create_plan` NO crea un gasto por el total — genera el calendario
completo de `installments` futuras (`domain/installments.py`). Solo
`pay_installment` mueve dinero de verdad, y por eso es la única función
de este módulo que no hace commit (pasa por
`core/idempotency.py::handle_idempotent_write`, igual que `transactions`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from datetime import date as date_
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ConflictError, NotFoundError, ValidationAppError
from domain.balances import LedgerEntry, signed_delta
from domain.installments import generate_installment_schedule
from schemas.installments import (
    InstallmentPayRequest,
    InstallmentPlanCreate,
    InstallmentPlanOut,
    InstallmentPlanUpdate,
)
from services.category_requirement import ensure_category_present
from services.change_log import record_change
from storage.models.account import Account
from storage.models.category import Category
from storage.models.installment import Installment, InstallmentPlan
from storage.models.transaction import Transaction


async def _get_owned_plan(db: AsyncSession, user_id: UUID, plan_id: UUID) -> InstallmentPlan:
    plan = await db.get(InstallmentPlan, plan_id)
    if plan is None or plan.user_id != user_id or plan.deleted_at is not None:
        raise NotFoundError("El plan de cuotas no existe.", code="INSTALLMENT_PLAN_NOT_FOUND")
    return plan


async def _get_owned_installment(
    db: AsyncSession, user_id: UUID, installment_id: UUID
) -> Installment:
    installment = await db.get(Installment, installment_id)
    if installment is None or installment.user_id != user_id:
        raise NotFoundError("La cuota no existe.", code="INSTALLMENT_NOT_FOUND")
    return installment


async def create_plan(
    db: AsyncSession, user_id: UUID, data: InstallmentPlanCreate
) -> InstallmentPlan:
    if len(data.installment_ids) != data.installments_count:
        raise ValidationAppError(
            "installment_ids debe tener exactamente installments_count elementos.",
            field="installment_ids",
            code="INSTALLMENT_IDS_COUNT_MISMATCH",
        )

    account = await db.get(Account, data.account_id)
    if account is None or account.user_id != user_id or account.deleted_at is not None:
        raise NotFoundError("La cuenta no existe.", code="ACCOUNT_NOT_FOUND")
    ensure_category_present("expense", data.category_id)  # un plan de cuotas siempre es gasto
    if data.category_id is not None:
        category = await db.get(Category, data.category_id)
        if category is None or category.user_id != user_id or category.deleted_at is not None:
            raise NotFoundError("La categoría no existe.", code="CATEGORY_NOT_FOUND")

    plan = InstallmentPlan(
        id=data.id,
        user_id=user_id,
        account_id=data.account_id,
        category_id=data.category_id,
        description=data.description,
        merchant=data.merchant,
        total_amount_cents=data.total_amount_cents,
        installments_count=data.installments_count,
        first_payment_date=data.first_payment_date,
        monthly_interest_rate=data.monthly_interest_rate,
    )
    db.add(plan)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            "Ya existe un plan de cuotas con ese id.", code="INSTALLMENT_PLAN_ID_TAKEN"
        ) from exc

    schedule = generate_installment_schedule(
        data.total_amount_cents,
        data.installments_count,
        data.first_payment_date,
        data.monthly_interest_rate,
    )
    for entry, installment_id in zip(schedule, data.installment_ids, strict=True):
        db.add(
            Installment(
                id=installment_id,
                user_id=user_id,
                plan_id=plan.id,
                number=entry.number,
                due_date=entry.due_date,
                amount_cents=entry.amount_cents,
                principal_cents=entry.principal_cents,
                interest_cents=entry.interest_cents,
            )
        )

    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="installment_plan",
        op="upsert",
        entity=plan,
        payload=InstallmentPlanOut.model_validate(plan).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(plan)
    return plan


async def list_plans(db: AsyncSession, user_id: UUID) -> list[InstallmentPlan]:
    result = await db.execute(
        select(InstallmentPlan).where(
            InstallmentPlan.user_id == user_id, InstallmentPlan.deleted_at.is_(None)
        )
    )
    return list(result.scalars().all())


async def get_plan(db: AsyncSession, user_id: UUID, plan_id: UUID) -> InstallmentPlan:
    return await _get_owned_plan(db, user_id, plan_id)


async def get_schedule(db: AsyncSession, user_id: UUID, plan_id: UUID) -> list[Installment]:
    await _get_owned_plan(db, user_id, plan_id)
    result = await db.execute(
        select(Installment)
        .where(Installment.plan_id == plan_id, Installment.user_id == user_id)
        .order_by(Installment.number)
    )
    return list(result.scalars().all())


async def update_plan(
    db: AsyncSession, user_id: UUID, plan_id: UUID, data: InstallmentPlanUpdate
) -> InstallmentPlan:
    plan = await _get_owned_plan(db, user_id, plan_id)
    changes = data.model_dump(exclude_unset=True)
    if "category_id" in changes:
        ensure_category_present("expense", changes["category_id"])
    for field, value in changes.items():
        setattr(plan, field, value)
    plan.updated_at = datetime.now(UTC)
    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="installment_plan",
        op="upsert",
        entity=plan,
        payload=InstallmentPlanOut.model_validate(plan).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(plan)
    return plan


async def delete_plan(db: AsyncSession, user_id: UUID, plan_id: UUID) -> None:
    """Cancela el plan y borra las cuotas NO pagadas (las pagadas quedan,
    ligadas a su transacción, como historial)."""
    plan = await _get_owned_plan(db, user_id, plan_id)
    await db.execute(
        delete(Installment).where(Installment.plan_id == plan.id, Installment.status == "pending")
    )
    plan.status = "cancelled"
    plan.deleted_at = datetime.now(UTC)
    plan.updated_at = datetime.now(UTC)
    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="installment_plan",
        op="delete",
        entity=plan,
        payload={"id": str(plan.id)},
    )
    await db.commit()


async def pay_installment(
    db: AsyncSession, user_id: UUID, installment_id: UUID, data: InstallmentPayRequest
) -> Installment:
    installment = await _get_owned_installment(db, user_id, installment_id)
    if installment.status != "pending":
        raise ConflictError("Esa cuota ya no está pendiente.", code="INSTALLMENT_NOT_PENDING")
    plan = await _get_owned_plan(db, user_id, installment.plan_id)
    # Este camino crea la transacción sin pasar por `create_transaction`.
    ensure_category_present("expense", plan.category_id)
    account = await db.get(Account, plan.account_id)
    assert account is not None  # invariante: la cuenta del plan no se borra físicamente

    now = datetime.now(UTC)
    transaction = Transaction(
        id=data.id,
        user_id=user_id,
        account_id=plan.account_id,
        category_id=plan.category_id,
        kind="expense",
        amount_cents=installment.amount_cents,
        currency=account.currency,
        date=data.date or date_.today(),
        description=f"Cuota {installment.number}/{plan.installments_count}: {plan.description}",
        installment_id=installment.id,
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
        LedgerEntry(kind="expense", amount_cents=installment.amount_cents),
        account.type,  # type: ignore[arg-type]
    )
    account.updated_at = now
    account.balance_recalculated_at = now

    installment.status = "paid"
    installment.paid_at = now
    installment.transaction_id = transaction.id

    remaining = await db.execute(
        select(func.count())
        .select_from(Installment)
        .where(Installment.plan_id == plan.id, Installment.status == "pending")
    )
    if remaining.scalar_one() == 0:
        plan.status = "completed"
        plan.updated_at = now

    await record_change(
        db,
        user_id=user_id,
        entity_type="transaction",
        op="upsert",
        entity=transaction,
        payload={"id": str(transaction.id), "installment_id": str(installment.id)},
    )
    await record_change(
        db,
        user_id=user_id,
        entity_type="installment_plan",
        op="upsert",
        entity=plan,
        payload=InstallmentPlanOut.model_validate(plan).model_dump(mode="json"),
    )
    return installment


async def list_upcoming(db: AsyncSession, user_id: UUID, days: int = 30) -> list[Installment]:
    today = date_.today()
    result = await db.execute(
        select(Installment)
        .where(
            Installment.user_id == user_id,
            Installment.status == "pending",
            Installment.due_date >= today,
            Installment.due_date <= today + timedelta(days=days),
        )
        .order_by(Installment.due_date)
    )
    return list(result.scalars().all())


async def get_liability(db: AsyncSession, user_id: UUID) -> tuple[int, list[tuple[str, int]]]:
    result = await db.execute(
        select(Installment.due_date, Installment.amount_cents).where(
            Installment.user_id == user_id, Installment.status == "pending"
        )
    )
    rows = result.all()
    total = sum(amount for _, amount in rows)
    by_month: dict[str, int] = {}
    for due_date, amount in rows:
        key = f"{due_date.year:04d}-{due_date.month:02d}"
        by_month[key] = by_month.get(key, 0) + amount
    return total, sorted(by_month.items())
