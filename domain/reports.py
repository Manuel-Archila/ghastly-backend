"""Agregaciones puras para /reports/dashboard, /reports/by-category y
/reports/cashflow: patrimonio neto, top categorías, desglose por categoría,
serie de flujo de caja y vista previa de vencimientos.

El service (`services/report_service.py`) junta los números desde la DB
(saldos de cuentas, deudas, sumas por categoría, listas de cuotas y
recurrentes) y se los pasa a este módulo — nada de esto toca SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from uuid import UUID

from domain.balances import LIABILITY_ACCOUNT_TYPES, AccountType
from domain.dates import add_months_clamped


def compute_net_worth(
    account_balances: list[tuple[AccountType, int]],
    unlinked_active_debt_total_cents: int,
) -> int:
    """Activos menos pasivos de cuentas, menos deudas activas sin cuenta
    vinculada (las vinculadas ya están reflejadas en el saldo de su cuenta,
    ver `services/debt_service.record_payment`)."""
    assets = sum(
        balance for kind, balance in account_balances if kind not in LIABILITY_ACCOUNT_TYPES
    )
    liabilities = sum(
        balance for kind, balance in account_balances if kind in LIABILITY_ACCOUNT_TYPES
    )
    return assets - liabilities - unlinked_active_debt_total_cents


@dataclass(frozen=True, slots=True)
class CategorySpend:
    category_id: UUID
    category_name: str
    net_spent_cents: int


def select_top_categories(rows: list[CategorySpend], limit: int = 5) -> list[CategorySpend]:
    """Excluye categorías con neto <= 0 (reembolsado más de lo gastado no es
    'top gasto'). Empata por nombre asc para que el orden sea estable."""
    positive = [row for row in rows if row.net_spent_cents > 0]
    return sorted(positive, key=lambda row: (-row.net_spent_cents, row.category_name))[:limit]


@dataclass(frozen=True, slots=True)
class CategoryBreakdownItem:
    category_id: UUID
    category_name: str
    amount_cents: int
    percent_of_total: int  # 0-100, entero (mismo redondeo que domain/budget.py)


def build_category_breakdown(rows: list[CategorySpend]) -> list[CategoryBreakdownItem]:
    """Para la dona de /reports/by-category: todas las categorías con
    movimiento neto positivo en el rango, ordenadas desc, con su % del
    total (para que la UI dibuje la dona sin tener que sumar ella misma)."""
    positive = [row for row in rows if row.net_spent_cents > 0]
    total = sum(row.net_spent_cents for row in positive)
    ordered = sorted(positive, key=lambda row: (-row.net_spent_cents, row.category_name))
    return [
        CategoryBreakdownItem(
            category_id=row.category_id,
            category_name=row.category_name,
            amount_cents=row.net_spent_cents,
            percent_of_total=_percent_of_total(row.net_spent_cents, total),
        )
        for row in ordered
    ]


def _percent_of_total(amount_cents: int, total_cents: int) -> int:
    if total_cents == 0:
        return 0
    return int(
        (Decimal(amount_cents) / Decimal(total_cents) * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


@dataclass(frozen=True, slots=True)
class UpcomingSource:
    source_type: Literal[
        "installment", "recurring", "card_statement", "card_payment", "debt_payment"
    ]
    source_id: UUID
    name: str
    due_date: date
    amount_cents: int
    # Caso 4: solo "recurring" puede no ser GTQ hoy (cuotas/deudas/tarjeta
    # no tienen columna currency — GTQ por construcción).
    currency: str = "GTQ"


def _sort_upcoming(sources: list[UpcomingSource]) -> list[UpcomingSource]:
    def sort_key(source: UpcomingSource) -> tuple[date, str, str]:
        return source.due_date, source.source_type, source.name

    return sorted(sources, key=sort_key)


def build_upcoming_preview(sources: list[UpcomingSource], limit: int = 5) -> list[UpcomingSource]:
    """Une cuotas + recurrentes ya filtrados por ventana de días, ordena por
    fecha y recorta a `limit` — vista previa liviana para el dashboard; el
    calendario completo (todas las fuentes, sin recorte) es
    `build_upcoming_calendar`, usado por GET /reports/upcoming."""
    return _sort_upcoming(sources)[:limit]


def build_upcoming_calendar(sources: list[UpcomingSource]) -> list[UpcomingSource]:
    """Igual que `build_upcoming_preview` pero sin recortar — GET
    /reports/upcoming ya filtró por ventana de días en el service, así que
    aquí solo se ordena."""
    return _sort_upcoming(sources)


def debt_balance_as_of(
    principal_cents: int, start_date: date, principal_payments: list[tuple[date, int]], as_of: date
) -> int:
    """Saldo de una deuda sin cuenta vinculada en una fecha pasada, para
    `/reports/net-worth`: principal menos lo abonado a capital hasta esa
    fecha (0 si la deuda todavía no existía). Las deudas vinculadas a una
    cuenta no pasan por acá — su historia ya vive en el saldo de esa cuenta,
    ver `compute_net_worth`."""
    if start_date > as_of:
        return 0
    paid_cents = sum(amount for pay_date, amount in principal_payments if pay_date <= as_of)
    return max(principal_cents - paid_cents, 0)


def trailing_months(end_month: str, months: int) -> list[str]:
    """Los últimos `months` meses ("YYYY-MM"), ascendente, terminando en
    `end_month` inclusive. Usado por /reports/net-worth, /trends y
    /savings-rate para no repetir la aritmética de meses en cada service."""
    if months <= 0:
        raise ValueError("months debe ser positivo")
    year, mon = (int(p) for p in end_month.split("-"))
    result = []
    for offset in range(months - 1, -1, -1):
        total = mon - 1 - offset
        y = year + total // 12
        m = total % 12 + 1
        result.append(f"{y:04d}-{m:02d}")
    return result


Granularity = Literal["month", "week"]


def generate_period_starts(date_from: date, date_to: date, granularity: Granularity) -> list[date]:
    """Los inicios de cada bucket (mes o semana) que caen en el rango,
    incluso los que no tienen movimientos — así la serie no tiene huecos
    para el frontend. Semanas alineadas a lunes, igual que
    `date_trunc('week', ...)` de Postgres (lo que agrupa el service)."""
    if granularity == "month":
        starts = []
        current = date(date_from.year, date_from.month, 1)
        last = date(date_to.year, date_to.month, 1)
        while current <= last:
            starts.append(current)
            current = add_months_clamped(current, 1)
        return starts

    starts = []
    current = date_from - timedelta(days=date_from.weekday())
    while current <= date_to:
        starts.append(current)
        current += timedelta(days=7)
    return starts


@dataclass(frozen=True, slots=True)
class CashflowPeriod:
    period_start: date
    income_cents: int
    expense_cents: int
    net_cents: int


def build_cashflow_series(
    rows: list[tuple[date, str, int]],
    period_starts: list[date],
) -> list[CashflowPeriod]:
    """`rows` ya viene agrupado por bucket + kind desde el service (SQL
    `date_trunc`); esto solo suma por período y rellena con ceros los
    buckets de `period_starts` que no aparecieron en `rows`."""
    income_by_period: dict[date, int] = {}
    expense_by_period: dict[date, int] = {}
    for period_start, kind, amount_cents in rows:
        if kind == "income":
            income_by_period[period_start] = income_by_period.get(period_start, 0) + amount_cents
        elif kind == "expense":
            expense_by_period[period_start] = expense_by_period.get(period_start, 0) + amount_cents

    return [
        CashflowPeriod(
            period_start=period_start,
            income_cents=income_by_period.get(period_start, 0),
            expense_cents=expense_by_period.get(period_start, 0),
            net_cents=income_by_period.get(period_start, 0)
            - expense_by_period.get(period_start, 0),
        )
        for period_start in period_starts
    ]


def _percent_change(from_cents: int, to_cents: int) -> int | None:
    """% de cambio de `from_cents` a `to_cents`, redondeado. `None` cuando
    la base es 0 y hay movimiento (el cambio no está definido: no existe un
    "% de aumento" desde cero) — la UI debe mostrar "nuevo", no un número."""
    if from_cents == 0:
        return 0 if to_cents == 0 else None
    return int(
        (Decimal(to_cents - from_cents) / Decimal(abs(from_cents)) * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


@dataclass(frozen=True, slots=True)
class PeriodTotals:
    income_cents: int
    expense_cents: int


@dataclass(frozen=True, slots=True)
class ComparisonTotals:
    a_income_cents: int
    a_expense_cents: int
    b_income_cents: int
    b_expense_cents: int
    income_change_percent: int | None
    expense_change_percent: int | None


def compare_totals(a: PeriodTotals, b: PeriodTotals) -> ComparisonTotals:
    """/reports/comparison: ingreso y gasto bruto de dos meses (`a` es la
    base, `b` el mes contra el que se compara) y su % de cambio."""
    return ComparisonTotals(
        a_income_cents=a.income_cents,
        a_expense_cents=a.expense_cents,
        b_income_cents=b.income_cents,
        b_expense_cents=b.expense_cents,
        income_change_percent=_percent_change(a.income_cents, b.income_cents),
        expense_change_percent=_percent_change(a.expense_cents, b.expense_cents),
    )


@dataclass(frozen=True, slots=True)
class CategoryComparisonItem:
    category_id: UUID
    category_name: str
    a_amount_cents: int
    b_amount_cents: int
    delta_cents: int
    percent_change: int | None


def build_category_comparison(
    a_rows: list[CategorySpend], b_rows: list[CategorySpend]
) -> list[CategoryComparisonItem]:
    """Gasto neto por categoría (misma convención de `_category_rows`: ya
    neteado de reembolsos) de los dos meses de /reports/comparison, unido
    por categoría — incluye las que solo tuvieron movimiento en uno de los
    dos meses. Ordenado por mayor variación absoluta."""
    names: dict[UUID, str] = {}
    a_by_id: dict[UUID, int] = {}
    b_by_id: dict[UUID, int] = {}
    for row in a_rows:
        names[row.category_id] = row.category_name
        a_by_id[row.category_id] = row.net_spent_cents
    for row in b_rows:
        names[row.category_id] = row.category_name
        b_by_id[row.category_id] = row.net_spent_cents

    items = [
        CategoryComparisonItem(
            category_id=category_id,
            category_name=category_name,
            a_amount_cents=a_by_id.get(category_id, 0),
            b_amount_cents=b_by_id.get(category_id, 0),
            delta_cents=b_by_id.get(category_id, 0) - a_by_id.get(category_id, 0),
            percent_change=_percent_change(
                a_by_id.get(category_id, 0), b_by_id.get(category_id, 0)
            ),
        )
        for category_id, category_name in names.items()
    ]
    return sorted(items, key=lambda item: (-abs(item.delta_cents), item.category_name))


def compute_income_averages(income_rows: list[tuple[int, bool]], months: int) -> tuple[int, int]:
    """Caso de negocio 12 (aguinaldo/bono 14): dos promedios mensuales de
    ingreso real (ya sin reembolsos, el service los excluye antes de armar
    `income_rows` como hace /reports/expected-income) sobre la ventana de
    `months` meses — uno que incluye los ingresos marcados
    `is_extraordinary` y otro que los excluye."""
    if months <= 0:
        raise ValueError("months debe ser positivo")
    total_with_extraordinary = sum(amount for amount, _ in income_rows)
    total_recurring = sum(
        amount for amount, is_extraordinary in income_rows if not is_extraordinary
    )
    return total_with_extraordinary // months, total_recurring // months


def compute_savings_rate(income_cents: int, expense_cents: int) -> int | None:
    """% del ingreso bruto del mes que no se gastó. `None` sin ingreso ese
    mes — la tasa de ahorro no está definida, no es 0 ni negativa."""
    if income_cents == 0:
        return None
    return int(
        (Decimal(income_cents - expense_cents) / Decimal(income_cents) * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
