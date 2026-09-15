"""Orquestación de presupuestos: DB + `domain/budget.py`.

`GET /budgets/current` es el endpoint más usado del catálogo — junta, por
categoría presupuestada: gastado (excluyendo transferencias), disponible,
% consumido, proyección de fin de mes y ritmo diario sugerido. Si el mes
ya está cerrado (`budget_periods`), se devuelve el resultado CONGELADO en
vez de recalcular — editar una transacción vieja no reescribe la historia
(regla de negocio 11).
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ConflictError, NotFoundError, ValidationAppError
from core.timezone import today_in_business_tz
from domain.budget import (
    compute_item_progress,
    compute_rollover_out,
    expected_income,
    project_period_end,
    suggested_daily_pace,
)
from schemas.budgets import (
    BudgetCreate,
    BudgetCurrentOut,
    BudgetHistoryOut,
    BudgetHistoryPeriodOut,
    BudgetItemCreate,
    BudgetItemUpdate,
    BudgetOut,
    BudgetUpdate,
    CategoryProgressOut,
    ClosePeriodResult,
    UnbudgetedCategoryOut,
)
from services import notification_preferences_service, push_service
from services.change_log import record_change
from services.query_filters import exclude_transfers
from storage.models.budget import Budget, BudgetItem, BudgetPeriod, BudgetPeriodItem
from storage.models.category import Category
from storage.models.transaction import Transaction

# ---------------------------------------------------------------------------
# Fechas de negocio


def _month_bounds(month: str) -> tuple[date, date]:
    year, mon = (int(p) for p in month.split("-"))
    last_day = calendar.monthrange(year, mon)[1]
    return date(year, mon, 1), date(year, mon, last_day)


def _current_month_str() -> str:
    today = today_in_business_tz()
    return f"{today.year:04d}-{today.month:02d}"


def _previous_month_str(month: str) -> str:
    year, mon = (int(p) for p in month.split("-"))
    if mon == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{mon - 1:02d}"


def _days_elapsed_and_total(period_start: date, period_end: date) -> tuple[int, int]:
    total_days = (period_end - period_start).days + 1
    today = today_in_business_tz()
    if today < period_start:
        return 0, total_days
    if today > period_end:
        return total_days, total_days
    return (today - period_start).days + 1, total_days


# ---------------------------------------------------------------------------
# Lookups con filtro por user_id


async def _get_owned(db: AsyncSession, user_id: UUID, budget_id: UUID) -> Budget:
    budget = await db.get(Budget, budget_id)
    if budget is None or budget.user_id != user_id or budget.deleted_at is not None:
        raise NotFoundError("El presupuesto no existe.", code="BUDGET_NOT_FOUND")
    return budget


async def _get_owned_item(
    db: AsyncSession, user_id: UUID, budget_id: UUID, item_id: UUID
) -> BudgetItem:
    item = await db.get(BudgetItem, item_id)
    if (
        item is None
        or item.user_id != user_id
        or item.budget_id != budget_id
        or item.deleted_at is not None
    ):
        raise NotFoundError("El ítem de presupuesto no existe.", code="BUDGET_ITEM_NOT_FOUND")
    return item


async def _get_owned_category(db: AsyncSession, user_id: UUID, category_id: UUID) -> Category:
    category = await db.get(Category, category_id)
    if category is None or category.user_id != user_id or category.deleted_at is not None:
        raise NotFoundError("La categoría no existe.", code="CATEGORY_NOT_FOUND")
    return category


# ---------------------------------------------------------------------------
# CRUD de presupuestos e ítems


async def create_budget(db: AsyncSession, user_id: UUID, data: BudgetCreate) -> Budget:
    budget = Budget(
        id=data.id,
        user_id=user_id,
        name=data.name,
        period_type=data.period_type,
        rollover_enabled=data.rollover_enabled,
        global_limit_cents=data.global_limit_cents,
        income_basis=data.income_basis,
        fixed_income_cents=data.fixed_income_cents,
    )
    db.add(budget)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("Ya existe un presupuesto con ese id.", code="BUDGET_ID_TAKEN") from exc

    for item_data in data.items:
        await _add_item(db, user_id, budget, item_data)

    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="budget",
        op="upsert",
        entity=budget,
        payload=BudgetOut.model_validate(budget).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(budget)
    return budget


async def _add_item(
    db: AsyncSession, user_id: UUID, budget: Budget, item_data: BudgetItemCreate
) -> BudgetItem:
    category = await _get_owned_category(db, user_id, item_data.category_id)
    if category.kind != "expense":
        raise ValidationAppError(
            "Solo se presupuestan categorías de gasto.",
            code="BUDGET_REQUIRES_EXPENSE_CATEGORY",
        )
    item = BudgetItem(
        id=item_data.id,
        user_id=user_id,
        budget_id=budget.id,
        category_id=item_data.category_id,
        amount_cents=item_data.amount_cents,
        rollover_enabled=item_data.rollover_enabled,
        sort_order=item_data.sort_order,
    )
    db.add(item)
    return item


async def add_item(
    db: AsyncSession, user_id: UUID, budget_id: UUID, data: BudgetItemCreate
) -> BudgetItem:
    budget = await _get_owned(db, user_id, budget_id)
    item = await _add_item(db, user_id, budget, data)
    await db.flush()
    await db.commit()
    await db.refresh(item)
    return item


async def list_budgets(db: AsyncSession, user_id: UUID) -> list[Budget]:
    result = await db.execute(
        select(Budget).where(Budget.user_id == user_id, Budget.deleted_at.is_(None))
    )
    return list(result.scalars().all())


async def get_budget(db: AsyncSession, user_id: UUID, budget_id: UUID) -> Budget:
    return await _get_owned(db, user_id, budget_id)


async def update_budget(
    db: AsyncSession, user_id: UUID, budget_id: UUID, data: BudgetUpdate
) -> Budget:
    budget = await _get_owned(db, user_id, budget_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(budget, field, value)
    budget.updated_at = datetime.now(UTC)
    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="budget",
        op="upsert",
        entity=budget,
        payload=BudgetOut.model_validate(budget).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(budget)
    return budget


async def delete_budget(db: AsyncSession, user_id: UUID, budget_id: UUID) -> None:
    budget = await _get_owned(db, user_id, budget_id)
    budget.deleted_at = datetime.now(UTC)
    budget.updated_at = datetime.now(UTC)
    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="budget",
        op="delete",
        entity=budget,
        payload={"id": str(budget.id)},
    )
    await db.commit()


async def update_item(
    db: AsyncSession, user_id: UUID, budget_id: UUID, item_id: UUID, data: BudgetItemUpdate
) -> BudgetItem:
    item = await _get_owned_item(db, user_id, budget_id, item_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    item.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(item)
    return item


# ---------------------------------------------------------------------------
# Ingreso esperado (caso de negocio 7)


async def income_for_month(db: AsyncSession, user_id: UUID, month: str) -> int:
    period_start, period_end = _month_bounds(month)
    stmt = exclude_transfers(
        select(func.coalesce(func.sum(Transaction.amount_cents), 0)).where(
            Transaction.user_id == user_id,
            Transaction.kind == "income",
            Transaction.deleted_at.is_(None),
            Transaction.date >= period_start,
            Transaction.date <= period_end,
        )
    )
    return int((await db.execute(stmt)).scalar_one())


async def _compute_expected_income(
    db: AsyncSession, user_id: UUID, budget: Budget, month: str
) -> int:
    previous_month = _previous_month_str(month)
    previous_month_income = await income_for_month(db, user_id, previous_month)

    months = [previous_month]
    for _ in range(2):
        months.append(_previous_month_str(months[-1]))
    avg_3m_income = sum([await income_for_month(db, user_id, m) for m in months]) // 3

    return expected_income(
        budget.income_basis,  # type: ignore[arg-type]
        fixed_cents=budget.fixed_income_cents or 0,
        previous_month_cents=previous_month_income,
        avg_3m_cents=avg_3m_income,
    )


# ---------------------------------------------------------------------------
# Cálculo de consumo por categoría (vivo, para un mes abierto)

# Un reembolso (caso de negocio 5) es una transacción `income` con
# `refund_of_id`: RESTA del gasto de su categoría original, nunca suma a
# ingresos. Todo cálculo de consumo suma gastos y resta reembolsos.
_spend_or_refund = or_(
    Transaction.kind == "expense",
    Transaction.refund_of_id.is_not(None),
)
_signed_spend = case(
    (Transaction.refund_of_id.is_not(None), -Transaction.amount_cents),
    else_=Transaction.amount_cents,
)


async def _spent_for_category(
    db: AsyncSession, user_id: UUID, category_id: UUID, period_start: date, period_end: date
) -> int:
    stmt = exclude_transfers(
        select(func.coalesce(func.sum(_signed_spend), 0)).where(
            Transaction.user_id == user_id,
            Transaction.category_id == category_id,
            _spend_or_refund,
            Transaction.deleted_at.is_(None),
            Transaction.date >= period_start,
            Transaction.date <= period_end,
        )
    )
    return int((await db.execute(stmt)).scalar_one())


async def _rollover_in_by_category(
    db: AsyncSession, budget_id: UUID, month: str
) -> dict[UUID, int]:
    previous_month = _previous_month_str(month)
    period_result = await db.execute(
        select(BudgetPeriod).where(
            BudgetPeriod.budget_id == budget_id, BudgetPeriod.month == previous_month
        )
    )
    period = period_result.scalar_one_or_none()
    if period is None:
        return {}
    items_result = await db.execute(
        select(BudgetPeriodItem).where(BudgetPeriodItem.budget_period_id == period.id)
    )
    return {i.category_id: i.rollover_out_cents for i in items_result.scalars().all()}


async def _compute_live_progress(
    db: AsyncSession,
    user_id: UUID,
    items: list[BudgetItem],
    month: str,
    period_start: date,
    period_end: date,
) -> tuple[list[CategoryProgressOut], int, int, int]:
    """Devuelve (progresos, total_presupuestado, días_transcurridos, días_totales)."""
    days_elapsed, days_total = _days_elapsed_and_total(period_start, period_end)
    days_remaining = max(0, days_total - days_elapsed)
    rollover_in_by_category = (
        await _rollover_in_by_category(db, items[0].budget_id, month) if items else {}
    )

    progresses: list[CategoryProgressOut] = []
    total_budgeted = 0
    for item in items:
        category = await db.get(Category, item.category_id)
        spent_cents = await _spent_for_category(
            db, user_id, item.category_id, period_start, period_end
        )
        rollover_in = rollover_in_by_category.get(item.category_id, 0)

        progress = compute_item_progress(
            budgeted_cents=item.amount_cents, spent_cents=spent_cents, rollover_in_cents=rollover_in
        )
        projected = project_period_end(spent_cents, days_elapsed, days_total)
        pace = suggested_daily_pace(progress.available_cents, days_remaining)

        progresses.append(
            CategoryProgressOut(
                category_id=item.category_id,
                category_name=category.name if category else "",
                budgeted_cents=progress.budgeted_cents,
                rollover_in_cents=progress.rollover_in_cents,
                spent_cents=progress.spent_cents,
                available_cents=progress.available_cents,
                percent_consumed=progress.percent_consumed,
                projected_cents=projected,
                suggested_daily_pace_cents=pace,
            )
        )
        total_budgeted += item.amount_cents

    return progresses, total_budgeted, days_elapsed, days_total


def _frozen_progress(item: BudgetPeriodItem, category_name: str) -> CategoryProgressOut:
    progress = compute_item_progress(
        budgeted_cents=item.budgeted_cents,
        spent_cents=item.spent_cents,
        rollover_in_cents=item.rollover_in_cents,
    )
    return CategoryProgressOut(
        category_id=item.category_id,
        category_name=category_name,
        budgeted_cents=progress.budgeted_cents,
        rollover_in_cents=progress.rollover_in_cents,
        spent_cents=progress.spent_cents,
        available_cents=progress.available_cents,
        percent_consumed=progress.percent_consumed,
        projected_cents=item.spent_cents,  # el mes ya terminó: lo proyectado es lo real
        suggested_daily_pace_cents=0,
    )


# ---------------------------------------------------------------------------
# El endpoint más usado del catálogo


async def get_current(
    db: AsyncSession, user_id: UUID, month: str | None = None
) -> BudgetCurrentOut:
    result = await db.execute(
        select(Budget).where(
            Budget.user_id == user_id, Budget.is_active.is_(True), Budget.deleted_at.is_(None)
        )
    )
    budget = result.scalar_one_or_none()
    if budget is None:
        raise NotFoundError("No hay un presupuesto activo.", code="NO_ACTIVE_BUDGET")

    month = month or _current_month_str()
    period_start, period_end = _month_bounds(month)

    closed_result = await db.execute(
        select(BudgetPeriod).where(BudgetPeriod.budget_id == budget.id, BudgetPeriod.month == month)
    )
    closed_period = closed_result.scalar_one_or_none()

    if closed_period is not None:
        period_items_result = await db.execute(
            select(BudgetPeriodItem).where(BudgetPeriodItem.budget_period_id == closed_period.id)
        )
        frozen_items = list(period_items_result.scalars().all())
        progresses = []
        for frozen in frozen_items:
            category = await db.get(Category, frozen.category_id)
            progresses.append(_frozen_progress(frozen, category.name if category else ""))
        total_budgeted = sum(i.budgeted_cents for i in frozen_items)
        total_spent = sum(i.spent_cents for i in frozen_items)
        return BudgetCurrentOut(
            month=month,
            is_closed=True,
            expected_income_cents=closed_period.expected_income_cents,
            total_budgeted_cents=total_budgeted,
            total_spent_cents=total_spent,
            total_available_cents=total_budgeted - total_spent,
            global_limit_cents=budget.global_limit_cents,
            global_projected_cents=total_spent,
            items=progresses,
            unbudgeted=[],
        )

    items_result = await db.execute(
        select(BudgetItem)
        .where(BudgetItem.budget_id == budget.id, BudgetItem.deleted_at.is_(None))
        .order_by(BudgetItem.sort_order)
    )
    items = list(items_result.scalars().all())
    progresses, total_budgeted, days_elapsed, days_total = await _compute_live_progress(
        db, user_id, items, month, period_start, period_end
    )

    total_spent_stmt = exclude_transfers(
        select(func.coalesce(func.sum(_signed_spend), 0)).where(
            Transaction.user_id == user_id,
            _spend_or_refund,
            Transaction.deleted_at.is_(None),
            Transaction.date >= period_start,
            Transaction.date <= period_end,
        )
    )
    total_spent = (await db.execute(total_spent_stmt)).scalar_one()

    budgeted_category_ids = {item.category_id for item in items}
    unbudgeted_stmt = exclude_transfers(
        select(Transaction.category_id, func.sum(_signed_spend))
        .where(
            Transaction.user_id == user_id,
            _spend_or_refund,
            Transaction.deleted_at.is_(None),
            Transaction.category_id.is_not(None),
            Transaction.date >= period_start,
            Transaction.date <= period_end,
        )
        .group_by(Transaction.category_id)
    )
    unbudgeted_rows = (await db.execute(unbudgeted_stmt)).all()
    unbudgeted: list[UnbudgetedCategoryOut] = []
    for category_id, spent in unbudgeted_rows:
        if category_id in budgeted_category_ids or spent <= 0:
            continue
        category = await db.get(Category, category_id)
        unbudgeted.append(
            UnbudgetedCategoryOut(
                category_id=category_id,
                category_name=category.name if category else "",
                spent_cents=spent,
            )
        )

    expected_income_cents = await _compute_expected_income(db, user_id, budget, month)
    global_projected = project_period_end(total_spent, days_elapsed, days_total)

    return BudgetCurrentOut(
        month=month,
        is_closed=False,
        expected_income_cents=expected_income_cents,
        total_budgeted_cents=total_budgeted,
        total_spent_cents=total_spent,
        total_available_cents=total_budgeted - total_spent,
        global_limit_cents=budget.global_limit_cents,
        global_projected_cents=global_projected,
        items=progresses,
        unbudgeted=unbudgeted,
    )


# ---------------------------------------------------------------------------
# Alertas de presupuesto (umbral 80%/100%, PLAN-backend.md §9)
#
# `check_alerts_for_category` es el gancho "tras cada escritura": lo llama
# `transaction_service` justo después de crear/editar un gasto, dentro de
# la misma transacción de DB, así que ve el consumo ya actualizado sin
# esperar a un commit. `jobs/check_budget_alerts.py` sigue corriendo a las
# 20:00 para cubrir presupuestos sin movimiento ese día (el gancho no
# reemplaza la corrida programada, la complementa). `push_service.notify_budget_alert`
# es quien evita mandar el push dos veces (dedup en `budget_alerts_sent`,
# válido igual si el gancho y el job caen el mismo umbral el mismo mes).
#
# Los umbrales salen de `notification_preferences.budget_alert_thresholds`
# (default `{80,100}`, PLAN-backend.md §5) — configurables por el usuario
# desde Ajustes → Notificaciones, ya no un valor fijo en este módulo.

logger = structlog.get_logger("services.budget_service")


async def check_alerts_for_category(
    db: AsyncSession, user_id: UUID, category_id: UUID, month: str
) -> None:
    """No hace nada si el usuario no tiene presupuesto activo o la
    categoría no está presupuestada ese mes — no es un error, es el caso
    común de un gasto en una categoría sin límite."""
    try:
        current = await get_current(db, user_id, month)
    except NotFoundError:
        return

    item = next((i for i in current.items if i.category_id == category_id), None)
    if item is None:
        return

    prefs = await notification_preferences_service.get_or_create(db, user_id)
    thresholds = sorted(prefs.budget_alert_thresholds, reverse=True)
    threshold = next((t for t in thresholds if item.percent_consumed >= t), None)
    if threshold is not None:
        logger.info(
            "budget_alert",
            user_id=str(user_id),
            category_id=str(category_id),
            percent_consumed=item.percent_consumed,
            threshold=threshold,
        )
        await push_service.notify_budget_alert(
            db, user_id, category_id, item.category_name, month, item.percent_consumed, threshold
        )


# ---------------------------------------------------------------------------
# Cierre de período (congela la historia) e historial


async def close_period(
    db: AsyncSession, user_id: UUID, budget_id: UUID, month: str | None = None
) -> ClosePeriodResult:
    budget = await _get_owned(db, user_id, budget_id)
    month = month or _previous_month_str(_current_month_str())
    period_start, period_end = _month_bounds(month)

    existing = await db.execute(
        select(BudgetPeriod).where(BudgetPeriod.budget_id == budget.id, BudgetPeriod.month == month)
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("Ese mes ya está cerrado.", code="PERIOD_ALREADY_CLOSED")

    items_result = await db.execute(
        select(BudgetItem).where(BudgetItem.budget_id == budget.id, BudgetItem.deleted_at.is_(None))
    )
    items = list(items_result.scalars().all())
    progresses, _, _, _ = await _compute_live_progress(
        db, user_id, items, month, period_start, period_end
    )
    expected_income_cents = await _compute_expected_income(db, user_id, budget, month)

    period = BudgetPeriod(
        user_id=user_id,
        budget_id=budget.id,
        month=month,
        period_start=period_start,
        period_end=period_end,
        expected_income_cents=expected_income_cents,
    )
    db.add(period)
    await db.flush()

    period_items_payload = []
    for progress, item in zip(progresses, items, strict=True):
        rollover_enabled = (
            item.rollover_enabled if item.rollover_enabled is not None else budget.rollover_enabled
        )
        rollover_out = compute_rollover_out(
            progress.available_cents, rollover_enabled=rollover_enabled
        )
        db.add(
            BudgetPeriodItem(
                budget_period_id=period.id,
                category_id=item.category_id,
                budgeted_cents=progress.budgeted_cents,
                rollover_in_cents=progress.rollover_in_cents,
                spent_cents=progress.spent_cents,
                rollover_out_cents=rollover_out,
            )
        )
        period_items_payload.append(
            {
                "category_id": str(item.category_id),
                "budgeted_cents": progress.budgeted_cents,
                "spent_cents": progress.spent_cents,
                "rollover_out_cents": rollover_out,
            }
        )

    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="budget_period",
        op="upsert",
        entity=period,
        payload={"id": str(period.id), "month": month, "items": period_items_payload},
    )
    await db.commit()
    return ClosePeriodResult(month=month, items=progresses)


async def get_history(db: AsyncSession, user_id: UUID, budget_id: UUID) -> BudgetHistoryOut:
    budget = await _get_owned(db, user_id, budget_id)
    periods_result = await db.execute(
        select(BudgetPeriod)
        .where(BudgetPeriod.budget_id == budget.id)
        .order_by(BudgetPeriod.month.desc())
    )
    periods = list(periods_result.scalars().all())

    history_periods: list[BudgetHistoryPeriodOut] = []
    for period in periods:
        items_result = await db.execute(
            select(BudgetPeriodItem).where(BudgetPeriodItem.budget_period_id == period.id)
        )
        progresses = []
        for item in items_result.scalars().all():
            category = await db.get(Category, item.category_id)
            progresses.append(_frozen_progress(item, category.name if category else ""))
        history_periods.append(
            BudgetHistoryPeriodOut(month=period.month, closed_at=period.closed_at, items=progresses)
        )
    return BudgetHistoryOut(periods=history_periods)


async def copy_from_previous(db: AsyncSession, user_id: UUID, budget_id: UUID) -> Budget:
    """ "Copiar el presupuesto del mes anterior": trae los montos que
    quedaron CONGELADOS al cerrar el mes previo (no los `budget_items`
    actuales, que pudieron cambiar desde entonces)."""
    budget = await _get_owned(db, user_id, budget_id)
    previous_month = _previous_month_str(_current_month_str())

    period_result = await db.execute(
        select(BudgetPeriod).where(
            BudgetPeriod.budget_id == budget.id, BudgetPeriod.month == previous_month
        )
    )
    period = period_result.scalar_one_or_none()
    if period is None:
        raise NotFoundError(
            "El mes anterior todavía no está cerrado.", code="PREVIOUS_PERIOD_NOT_CLOSED"
        )

    prev_items_result = await db.execute(
        select(BudgetPeriodItem).where(BudgetPeriodItem.budget_period_id == period.id)
    )
    prev_items = list(prev_items_result.scalars().all())

    current_items_result = await db.execute(
        select(BudgetItem).where(BudgetItem.budget_id == budget.id, BudgetItem.deleted_at.is_(None))
    )
    current_by_category = {i.category_id: i for i in current_items_result.scalars().all()}

    for prev_item in prev_items:
        current = current_by_category.get(prev_item.category_id)
        if current is not None:
            current.amount_cents = prev_item.budgeted_cents
            current.updated_at = datetime.now(UTC)
        else:
            db.add(
                BudgetItem(
                    id=uuid4(),
                    user_id=user_id,
                    budget_id=budget.id,
                    category_id=prev_item.category_id,
                    amount_cents=prev_item.budgeted_cents,
                )
            )

    await db.commit()
    await db.refresh(budget)
    return budget
