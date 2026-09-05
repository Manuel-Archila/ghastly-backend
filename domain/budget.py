"""Presupuestos: consumo, disponible, proyección y rollover.

Funciones puras — el service (`services/budget_service.py`) es quien junta
`spent_cents` desde la DB (sumando transacciones, ya sin transferencias
por `exclude_transfers()`) y le pasa los números a este módulo.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

IncomeBasis = Literal["fixed", "previous_month", "avg_3m"]


@dataclass(frozen=True, slots=True)
class BudgetItemProgress:
    budgeted_cents: int
    rollover_in_cents: int
    spent_cents: int
    available_cents: int
    percent_consumed: int  # 0-100+, entero (regla de negocio: "Entero: 78%")


def compute_item_progress(
    *, budgeted_cents: int, spent_cents: int, rollover_in_cents: int = 0
) -> BudgetItemProgress:
    """`available` puede ser negativo (sobregiro) — la UI lo pinta desbordado,
    no lo recorta al 100% (PLAN-frontend §6.5)."""
    total_budget = budgeted_cents + rollover_in_cents
    available = total_budget - spent_cents

    if total_budget == 0:
        percent = 0 if spent_cents == 0 else 100
    else:
        percent = int(
            (Decimal(spent_cents) / Decimal(total_budget) * 100).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )

    return BudgetItemProgress(
        budgeted_cents=budgeted_cents,
        rollover_in_cents=rollover_in_cents,
        spent_cents=spent_cents,
        available_cents=available,
        percent_consumed=percent,
    )


def compute_rollover_out(available_cents: int, *, rollover_enabled: bool) -> int:
    """Al cerrar el período: solo el SOBRANTE pasa al mes siguiente.

    Un sobregiro no se arrastra como deuda del próximo mes — cada mes
    empieza limpio salvo que haya superávit y el rollover esté activo.
    """
    if not rollover_enabled or available_cents <= 0:
        return 0
    return available_cents


def project_period_end(spent_cents: int, days_elapsed: int, days_in_period: int) -> int:
    """ "A este ritmo terminás en Q X": extrapola el ritmo real de gasto
    (lo gastado hasta hoy / días transcurridos) al resto del período."""
    if days_elapsed <= 0:
        return spent_cents
    daily_rate = Decimal(spent_cents) / Decimal(days_elapsed)
    projected = (daily_rate * days_in_period).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(projected)


def suggested_daily_pace(available_cents: int, days_remaining: int) -> int:
    """ "~Q 30/día": cuánto se puede gastar por día sin pasarse, con lo que queda."""
    if days_remaining <= 0:
        return available_cents
    return int(
        (Decimal(available_cents) / Decimal(days_remaining)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def expected_income(
    basis: IncomeBasis,
    *,
    fixed_cents: int,
    previous_month_cents: int,
    avg_3m_cents: int,
) -> int:
    """Caso de negocio 7: el presupuesto no asume un sueldo fijo por defecto."""
    if basis == "fixed":
        return fixed_cents
    if basis == "previous_month":
        return previous_month_cents
    if basis == "avg_3m":
        return avg_3m_cents
    raise ValueError(f"income_basis desconocido: {basis}")
