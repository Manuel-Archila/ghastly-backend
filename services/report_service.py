"""Orquestación de /reports/dashboard, /reports/by-category y
/reports/cashflow: DB + `domain/reports.py`.

Dashboard: una sola llamada para la pantalla "Hoy" — patrimonio neto, flujo
del mes, top 5 categorías de gasto, presupuesto global (reusa
`budget_service`), vista previa de próximos vencimientos, pasivo en cuotas
y el stub de "por cobrar" (Fase 5, todavía no existe esa feature).

By-category: desglose completo por categoría para la dona, de gasto o de
ingreso, en cualquier rango de fechas.

Cashflow: serie de ingreso/gasto por mes o semana, sin huecos, para
graficar tendencia.

Net-worth: patrimonio neto reconstruido mes a mes reproduciendo el ledger
de cada cuenta y deuda (mismas funciones de `domain/balances.py` que usa
`POST /accounts/{id}/recalculate`), no un snapshot guardado.

Trends: la misma serie de cashflow por mes, más los dos promedios de
ingreso del caso de negocio 12 (aguinaldo/bono 14).

Comparison: ingreso/gasto y desglose por categoría de dos meses, uno junto
al otro.

Anomalies: gasto por categoría del mes contra el promedio de los 3
anteriores — "gastaste 40% más en X".

Savings-rate: % del ingreso bruto no gastado, por mes.

Upcoming: calendario completo (sin recorte de 5) de cuotas, recurrentes,
corte/pago de tarjetas y pago mensual de deudas activas.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Literal
from uuid import UUID

from sqlalchemy import Date as SqlDate
from sqlalchemy import and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from core.errors import NotFoundError
from core.timezone import today_in_business_tz
from domain.anomalies import detect_anomalies
from domain.balances import LedgerEntry, compute_balance_series
from domain.credit_cycle import compute_current_cycle
from domain.dates import next_day_of_month
from domain.reports import (
    CategorySpend,
    Granularity,
    PeriodTotals,
    UpcomingSource,
    build_cashflow_series,
    build_category_breakdown,
    build_category_comparison,
    build_upcoming_calendar,
    build_upcoming_preview,
    compare_totals,
    compute_income_averages,
    compute_net_worth,
    compute_savings_rate,
    debt_balance_as_of,
    generate_period_starts,
    select_top_categories,
    trailing_months,
)
from schemas.reports import (
    AnomaliesOut,
    CashflowOut,
    CashflowPeriodOut,
    CashflowSeriesOut,
    CategoryAnomalyOut,
    CategoryBreakdownItemOut,
    CategoryBreakdownOut,
    CategoryComparisonItemOut,
    ComparisonOut,
    DashboardOut,
    ExpectedIncomeOut,
    InstallmentLiabilityOut,
    MonthAmountOut,
    NetWorthHistoryOut,
    NetWorthOut,
    SavingsRateOut,
    SavingsRatePointOut,
    TopCategoryOut,
    TrendsOut,
    UpcomingCalendarOut,
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
from storage.models.debt import DebtPayment
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


def _previous_month_str(month: str) -> str:
    year, mon = (int(p) for p in month.split("-"))
    if mon == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{mon - 1:02d}"


# ---------------------------------------------------------------------------
# Sumas por categoría (top-5 del dashboard y /reports/by-category)
#
# Gasto: mismo neteo de reembolsos que `budget_service._signed_spend`/
# `_spend_or_refund` (caso de negocio 5: un reembolso resta del gasto de su
# categoría original). Se duplica a propósito en vez de importar los
# privados de budget_service — ver decisión de diseño en el plan de
# /reports/dashboard.
# Ingreso: un reembolso NUNCA es ingreso (caso 5), así que se excluye del
# todo — no se neteá, se ignora.

_spend_or_refund = or_(
    Transaction.kind == "expense",
    Transaction.refund_of_id.is_not(None),
)
_signed_spend = case(
    (Transaction.refund_of_id.is_not(None), -Transaction.amount_cents),
    else_=Transaction.amount_cents,
)
_real_income = and_(Transaction.kind == "income", Transaction.refund_of_id.is_(None))


async def _category_rows(
    db: AsyncSession,
    user_id: UUID,
    date_from: date,
    date_to: date,
    kind: Literal["expense", "income"],
) -> list[CategorySpend]:
    condition: ColumnElement[bool] = _spend_or_refund if kind == "expense" else _real_income
    amount_expr = _signed_spend if kind == "expense" else Transaction.amount_cents

    stmt = exclude_transfers(
        select(Category.id, Category.name, func.coalesce(func.sum(amount_expr), 0))
        .join(Transaction, Transaction.category_id == Category.id)
        .where(
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            condition,
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


# ---------------------------------------------------------------------------
# /reports/net-worth: reconstrucción histórica de saldos
#
# `current_balance_cents` es una caché de HOY (CLAUDE.md regla 6); para un
# punto en el pasado no sirve, así que se reproduce el ledger completo de
# cada cuenta con `domain.balances.compute_balance_series` — la misma
# función que usaría `POST /accounts/{id}/recalculate`, pero evaluada en
# varios cortes de fecha en una sola pasada en vez de recalcular desde cero
# por mes. Se incluyen cuentas archivadas: archivar no borra el patrimonio
# que esa cuenta representó en su momento.


async def _account_ledger(db: AsyncSession, account_id: UUID) -> list[tuple[date, LedgerEntry]]:
    stmt = (
        select(
            Transaction.date,
            Transaction.kind,
            Transaction.amount_cents,
            Transaction.transfer_direction,
        )
        .where(Transaction.account_id == account_id, Transaction.deleted_at.is_(None))
        .order_by(Transaction.date)
    )
    rows = (await db.execute(stmt)).all()
    return [
        (row[0], LedgerEntry(kind=row[1], amount_cents=row[2], transfer_direction=row[3]))
        for row in rows
    ]


async def _debt_principal_payments(db: AsyncSession, debt_id: UUID) -> list[tuple[date, int]]:
    stmt = (
        select(DebtPayment.date, DebtPayment.principal_cents)
        .where(DebtPayment.debt_id == debt_id)
        .order_by(DebtPayment.date)
    )
    rows = (await db.execute(stmt)).all()
    return [(row[0], int(row[1])) for row in rows]


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

    rows = await _category_rows(db, user_id, period_start, period_end, "expense")
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


async def get_category_breakdown(
    db: AsyncSession,
    user_id: UUID,
    date_from: date | None,
    date_to: date | None,
    kind: Literal["expense", "income"] = "expense",
) -> CategoryBreakdownOut:
    if date_from is None or date_to is None:
        default_start, default_end = _month_bounds(_current_month_str())
        date_from = date_from or default_start
        date_to = date_to or default_end

    rows = await _category_rows(db, user_id, date_from, date_to, kind)
    items = build_category_breakdown(rows)
    total_cents = sum(item.amount_cents for item in items)

    return CategoryBreakdownOut(
        kind=kind,
        from_date=date_from,
        to_date=date_to,
        total_cents=total_cents,
        items=[
            CategoryBreakdownItemOut(
                category_id=item.category_id,
                category_name=item.category_name,
                amount_cents=item.amount_cents,
                percent_of_total=item.percent_of_total,
            )
            for item in items
        ],
    )


async def get_cashflow_series(
    db: AsyncSession,
    user_id: UUID,
    date_from: date | None,
    date_to: date | None,
    granularity: Granularity = "month",
) -> CashflowSeriesOut:
    if date_from is None or date_to is None:
        default_start, default_end = _month_bounds(_current_month_str())
        date_from = date_from or default_start
        date_to = date_to or default_end

    # Vista de caja bruta, igual que el flujo del mes de /reports/dashboard:
    # NO se netean los reembolsos (kind=income tal cual quedó guardado).
    period_expr = cast(func.date_trunc(granularity, Transaction.date), SqlDate)
    stmt = exclude_transfers(
        select(period_expr, Transaction.kind, func.sum(Transaction.amount_cents))
        .where(
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
            Transaction.date >= date_from,
            Transaction.date <= date_to,
        )
        .group_by(period_expr, Transaction.kind)
    )
    rows = [(row[0], row[1], int(row[2])) for row in (await db.execute(stmt)).all()]

    period_starts = generate_period_starts(date_from, date_to, granularity)
    periods = build_cashflow_series(rows, period_starts)

    return CashflowSeriesOut(
        granularity=granularity,
        from_date=date_from,
        to_date=date_to,
        periods=[
            CashflowPeriodOut(
                period_start=period.period_start,
                income_cents=period.income_cents,
                expense_cents=period.expense_cents,
                net_cents=period.net_cents,
            )
            for period in periods
        ],
    )


async def get_expected_income(
    db: AsyncSession, user_id: UUID, month: str | None
) -> ExpectedIncomeOut:
    month = month or _current_month_str()
    previous_month = _previous_month_str(month)
    previous_month_cents = await budget_service.income_for_month(db, user_id, previous_month)

    months = [previous_month]
    for _ in range(2):
        months.append(_previous_month_str(months[-1]))
    avg_3m_cents = sum([await budget_service.income_for_month(db, user_id, m) for m in months]) // 3

    return ExpectedIncomeOut(
        month=month, previous_month_cents=previous_month_cents, avg_3m_cents=avg_3m_cents
    )


async def get_net_worth_history(
    db: AsyncSession, user_id: UUID, months: int | None
) -> NetWorthHistoryOut:
    months = months or 12
    today = today_in_business_tz()
    month_list = trailing_months(_current_month_str(), months)
    as_of_dates = []
    for month in month_list:
        _, month_end = _month_bounds(month)
        as_of_dates.append(min(month_end, today))

    accounts = await account_service.list_accounts(db, user_id, include_archived=True)
    per_account_balances = []
    for account in accounts:
        ledger = await _account_ledger(db, account.id)
        balances = compute_balance_series(
            account.initial_balance_cents,
            ledger,
            account.type,  # type: ignore[arg-type]
            as_of_dates,
        )
        per_account_balances.append((account.type, balances))

    unlinked_debts = [
        d for d in await debt_service.list_debts(db, user_id) if d.linked_account_id is None
    ]
    per_debt_balances = []
    for debt in unlinked_debts:
        payments = await _debt_principal_payments(db, debt.id)
        per_debt_balances.append(
            [
                debt_balance_as_of(debt.principal_cents, debt.start_date, payments, as_of)
                for as_of in as_of_dates
            ]
        )

    points = []
    for i, month in enumerate(month_list):
        account_balances_i = [(kind, balances[i]) for kind, balances in per_account_balances]
        unlinked_debt_total_i = sum(balances[i] for balances in per_debt_balances)
        net_worth_cents = compute_net_worth(account_balances_i, unlinked_debt_total_i)  # type: ignore[arg-type]
        points.append(MonthAmountOut(month=month, amount_cents=net_worth_cents))

    return NetWorthHistoryOut(months=months, points=points)


async def get_trends(db: AsyncSession, user_id: UUID, months: int | None) -> TrendsOut:
    months = months or 6
    month_list = trailing_months(_current_month_str(), months)
    date_from, _ = _month_bounds(month_list[0])
    _, date_to = _month_bounds(month_list[-1])

    period_expr = cast(func.date_trunc("month", Transaction.date), SqlDate)
    stmt = exclude_transfers(
        select(period_expr, Transaction.kind, func.sum(Transaction.amount_cents))
        .where(
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
            Transaction.date >= date_from,
            Transaction.date <= date_to,
        )
        .group_by(period_expr, Transaction.kind)
    )
    rows = [(row[0], row[1], int(row[2])) for row in (await db.execute(stmt)).all()]
    periods = build_cashflow_series(rows, generate_period_starts(date_from, date_to, "month"))

    # Ingreso real (sin reembolsos, regla de negocio 5) de la misma ventana,
    # partido por is_extraordinary para los dos promedios del caso 12.
    income_stmt = exclude_transfers(
        select(Transaction.amount_cents, Transaction.is_extraordinary).where(
            Transaction.user_id == user_id,
            _real_income,
            Transaction.deleted_at.is_(None),
            Transaction.date >= date_from,
            Transaction.date <= date_to,
        )
    )
    income_rows = [(int(row[0]), bool(row[1])) for row in (await db.execute(income_stmt)).all()]
    avg_with, avg_recurring = compute_income_averages(income_rows, months)

    return TrendsOut(
        months=months,
        periods=[
            CashflowPeriodOut(
                period_start=period.period_start,
                income_cents=period.income_cents,
                expense_cents=period.expense_cents,
                net_cents=period.net_cents,
            )
            for period in periods
        ],
        avg_income_with_extraordinary_cents=avg_with,
        avg_income_recurring_cents=avg_recurring,
    )


async def get_comparison(
    db: AsyncSession, user_id: UUID, a_month: str, b_month: str
) -> ComparisonOut:
    a_start, a_end = _month_bounds(a_month)
    b_start, b_end = _month_bounds(b_month)

    a_stats = await transaction_service.get_stats(db, user_id, date_from=a_start, date_to=a_end)
    b_stats = await transaction_service.get_stats(db, user_id, date_from=b_start, date_to=b_end)
    totals = compare_totals(
        PeriodTotals(a_stats.total_income_cents, a_stats.total_expense_cents),
        PeriodTotals(b_stats.total_income_cents, b_stats.total_expense_cents),
    )

    a_rows = await _category_rows(db, user_id, a_start, a_end, "expense")
    b_rows = await _category_rows(db, user_id, b_start, b_end, "expense")
    categories = build_category_comparison(a_rows, b_rows)

    return ComparisonOut(
        a_month=a_month,
        b_month=b_month,
        a_income_cents=totals.a_income_cents,
        a_expense_cents=totals.a_expense_cents,
        b_income_cents=totals.b_income_cents,
        b_expense_cents=totals.b_expense_cents,
        income_change_percent=totals.income_change_percent,
        expense_change_percent=totals.expense_change_percent,
        categories=[
            CategoryComparisonItemOut(
                category_id=item.category_id,
                category_name=item.category_name,
                a_amount_cents=item.a_amount_cents,
                b_amount_cents=item.b_amount_cents,
                delta_cents=item.delta_cents,
                percent_change=item.percent_change,
            )
            for item in categories
        ],
    )


# Cuántos meses anteriores promedia /reports/anomalies — mismo criterio que
# usará el job `detect_anomalies` (PLAN-backend.md §9, día 1 06:00).
_ANOMALY_WINDOW_MONTHS = 3


async def get_anomalies(db: AsyncSession, user_id: UUID, month: str | None) -> AnomaliesOut:
    month = month or _current_month_str()
    period_start, period_end = _month_bounds(month)
    current_rows = await _category_rows(db, user_id, period_start, period_end, "expense")

    window_months = trailing_months(_previous_month_str(month), _ANOMALY_WINDOW_MONTHS)
    window_start, _ = _month_bounds(window_months[0])
    _, window_end = _month_bounds(window_months[-1])
    previous_rows = await _category_rows(db, user_id, window_start, window_end, "expense")

    anomalies = detect_anomalies(current_rows, previous_rows, _ANOMALY_WINDOW_MONTHS)
    return AnomaliesOut(
        month=month,
        items=[
            CategoryAnomalyOut(
                category_id=item.category_id,
                category_name=item.category_name,
                current_cents=item.current_cents,
                average_cents=item.average_cents,
                percent_increase=item.percent_increase,
            )
            for item in anomalies
        ],
    )


async def get_savings_rate(db: AsyncSession, user_id: UUID, months: int | None) -> SavingsRateOut:
    months = months or 12
    month_list = trailing_months(_current_month_str(), months)
    date_from, _ = _month_bounds(month_list[0])
    _, date_to = _month_bounds(month_list[-1])

    cashflow = await get_cashflow_series(db, user_id, date_from, date_to, "month")
    points = [
        SavingsRatePointOut(
            month=f"{period.period_start.year:04d}-{period.period_start.month:02d}",
            income_cents=period.income_cents,
            expense_cents=period.expense_cents,
            savings_rate_percent=compute_savings_rate(period.income_cents, period.expense_cents),
        )
        for period in cashflow.periods
    ]
    return SavingsRateOut(months=months, points=points)


async def get_upcoming(db: AsyncSession, user_id: UUID, days: int) -> UpcomingCalendarOut:
    today = today_in_business_tz()

    installments = await installment_service.list_upcoming(db, user_id, days=days)
    recurring = await recurring_service.list_upcoming(db, user_id, days=days)
    plans_by_id = await _get_plans_by_id(db, {installment.plan_id for installment in installments})

    sources = [
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

    accounts = await account_service.list_accounts(db, user_id)
    for account in accounts:
        if (
            account.type != "credit_card"
            or account.statement_day is None
            or account.payment_due_day is None
        ):
            continue
        cycle = compute_current_cycle(today, account.statement_day, account.payment_due_day)
        if 0 <= cycle.days_until_statement <= days:
            sources.append(
                UpcomingSource(
                    source_type="card_statement",
                    source_id=account.id,
                    name=account.name,
                    due_date=cycle.statement_date,
                    amount_cents=0,
                )
            )
        if 0 <= cycle.days_until_payment_due <= days:
            sources.append(
                UpcomingSource(
                    source_type="card_payment",
                    source_id=account.id,
                    name=account.name,
                    due_date=cycle.payment_due_date,
                    amount_cents=account.current_balance_cents,
                )
            )

    # Deudas sin día de pago propio en el esquema: se asume el mismo día del
    # mes en que empezó (`start_date.day`), igual que el corte de tarjeta.
    debts = await debt_service.list_debts(db, user_id)
    for debt in debts:
        if debt.status != "active" or debt.monthly_payment_cents is None:
            continue
        due_date = next_day_of_month(today, debt.start_date.day, inclusive=True)
        days_until = (due_date - today).days
        if 0 <= days_until <= days:
            sources.append(
                UpcomingSource(
                    source_type="debt_payment",
                    source_id=debt.id,
                    name=debt.name,
                    due_date=due_date,
                    amount_cents=debt.monthly_payment_cents,
                )
            )

    items = build_upcoming_calendar(sources)
    return UpcomingCalendarOut(
        days=days,
        items=[
            UpcomingItemOut(
                source_type=item.source_type,
                source_id=item.source_id,
                name=item.name,
                due_date=item.due_date,
                amount_cents=item.amount_cents,
            )
            for item in items
        ],
    )
