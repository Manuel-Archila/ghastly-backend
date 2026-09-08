"""Orquestación de /reports/dashboard: DB + `domain/reports.py`.

Una sola llamada para la pantalla "Hoy": patrimonio neto, flujo del mes,
top 5 categorías de gasto, presupuesto global (reusa `budget_service`),
vista previa de próximos vencimientos, pasivo en cuotas y el stub de
"por cobrar" (Fase 5, todavía no existe esa feature).
"""

from __future__ import annotations

import calendar
from datetime import date
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import NotFoundError
from core.timezone import today_in_business_tz
from domain.reports import (
    CategorySpend,
    UpcomingSource,
    build_upcoming_preview,
    compute_net_worth,
    select_top_categories,
)
from schemas.reports import (
    CashflowOut,
    DashboardOut,
    InstallmentLiabilityOut,
    MonthAmountOut,
    NetWorthOut,
    TopCategoryOut,
    UpcomingItemOut,
)
from services import (
    account_service,
    budget_service,
    debt_service,
    installment_service,
    recurring_service,
    transaction_service,
)
from services.query_filters import exclude_transfers
from storage.models.category import Category
from storage.models.installment import InstallmentPlan
from storage.models.transaction import Transaction

# ---------------------------------------------------------------------------
# Fechas de negocio (mismo patrón que services/budget_service.py)


def _month_bounds(month: str) -> tuple[date, date]:
    year, mon = (int(p) for p in month.split("-"))
    last_day = calendar.monthrange(year, mon)[1]
    return date(year, mon, 1), date(year, mon, last_day)


def _current_month_str() -> str:
    today = today_in_business_tz()
    return f"{today.year:04d}-{today.month:02d}"


# ---------------------------------------------------------------------------
# Top 5 categorías de gasto
#
# Mismo neteo de reembolsos que `budget_service._signed_spend`/`_spend_or_refund`
# (caso de negocio 5: un reembolso resta del gasto de su categoría original).
# Se duplica a propósito en vez de importar los privados de budget_service —
# ver decisión de diseño en el plan de /reports/dashboard.

_spend_or_refund = or_(
    Transaction.kind == "expense",
    Transaction.refund_of_id.is_not(None),
)
_signed_spend = case(
    (Transaction.refund_of_id.is_not(None), -Transaction.amount_cents),
    else_=Transaction.amount_cents,
)


async def _top_category_rows(
    db: AsyncSession, user_id: UUID, period_start: date, period_end: date
) -> list[CategorySpend]:
    stmt = exclude_transfers(
        select(Category.id, Category.name, func.coalesce(func.sum(_signed_spend), 0))
        .join(Transaction, Transaction.category_id == Category.id)
        .where(
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
            Transaction.date >= period_start,
            Transaction.date <= period_end,
            _spend_or_refund,
        )
        .group_by(Category.id, Category.name)
    )
    rows = (await db.execute(stmt)).all()
    return [
        CategorySpend(category_id=row[0], category_name=row[1], net_spent_cents=int(row[2]))
        for row in rows
    ]


async def _get_plans_by_id(db: AsyncSession, plan_ids: set[UUID]) -> dict[UUID, InstallmentPlan]:
    if not plan_ids:
        return {}
    result = await db.execute(select(InstallmentPlan).where(InstallmentPlan.id.in_(plan_ids)))
    return {plan.id: plan for plan in result.scalars().all()}


async def get_dashboard(db: AsyncSession, user_id: UUID, month: str | None) -> DashboardOut:
    month = month or _current_month_str()
    period_start, period_end = _month_bounds(month)

    # Patrimonio neto: cuentas activas (no archivadas) menos deudas activas
    # sin cuenta vinculada (las vinculadas ya están reflejadas en el saldo
    # de su cuenta, ver services/debt_service.record_payment).
    accounts = await account_service.list_accounts(db, user_id)
    debts = await debt_service.list_debts(db, user_id)
    unlinked_active_debt_total_cents = sum(
        debt.balance_cents
        for debt in debts
        if debt.status == "active" and debt.linked_account_id is None
    )
    net_worth_cents = compute_net_worth(
        [(account.type, account.current_balance_cents) for account in accounts],  # type: ignore[misc]
        unlinked_active_debt_total_cents,
    )

    # Flujo del mes: vista de caja bruta (dinero que entró/salió), NO usa el
    # neteo de reembolsos que aplica budget_service a "consumo de categoría".
    stats = await transaction_service.get_stats(
        db, user_id, date_from=period_start, date_to=period_end
    )

    rows = await _top_category_rows(db, user_id, period_start, period_end)
    top_categories = select_top_categories(rows, limit=5)

    # El dashboard no depende de que exista un presupuesto activo — un
    # usuario nuevo debe poder ver su pantalla "Hoy" sin haber creado uno.
    try:
        budget = await budget_service.get_current(db, user_id, month)
    except NotFoundError:
        budget = None

    installments = await installment_service.list_upcoming(db, user_id, days=30)
    recurring = await recurring_service.list_upcoming(db, user_id, days=30)
    plans_by_id = await _get_plans_by_id(db, {installment.plan_id for installment in installments})
    upcoming_sources = [
        UpcomingSource(
            source_type="installment",
            source_id=installment.id,
            name=plans_by_id[installment.plan_id].description,
            due_date=installment.due_date,
            amount_cents=installment.amount_cents,
        )
        for installment in installments
    ] + [
        UpcomingSource(
            source_type="recurring",
            source_id=rule.id,
            name=rule.name,
            due_date=rule.next_due_date,
            amount_cents=rule.amount_cents,
        )
        for rule in recurring
    ]
    upcoming = build_upcoming_preview(upcoming_sources, limit=5)

    total_liability_cents, by_month = await installment_service.get_liability(db, user_id)

    return DashboardOut(
        month=month,
        net_worth=NetWorthOut(net_worth_cents=net_worth_cents),
        cashflow=CashflowOut(
            income_cents=stats.total_income_cents,
            expense_cents=stats.total_expense_cents,
            net_cents=stats.net_cents,
        ),
        top_categories=[
            TopCategoryOut(
                category_id=row.category_id,
                category_name=row.category_name,
                net_spent_cents=row.net_spent_cents,
            )
            for row in top_categories
        ],
        budget=budget,
        upcoming=[
            UpcomingItemOut(
                source_type=item.source_type,
                source_id=item.source_id,
                name=item.name,
                due_date=item.due_date,
                amount_cents=item.amount_cents,
            )
            for item in upcoming
        ],
        installment_liability=InstallmentLiabilityOut(
            total_pending_cents=total_liability_cents,
            by_month=[MonthAmountOut(month=m, amount_cents=a) for m, a in by_month],
        ),
        receivable_cents=None,
    )
