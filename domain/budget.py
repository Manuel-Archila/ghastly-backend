"""Presupuestos: consumo, disponible, proyección y rollover.

Funciones puras — el service (`services/budget_service.py`) es quien junta
`spent_cents` desde la DB (sumando transacciones, ya sin transferencias
por `exclude_transfers()`) y le pasa los números a este módulo.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
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


# ---------------------------------------------------------------------------
# Jerarquía padre/hijo (dos niveles, igual que `domain/categories.py`)
#
# El vínculo NO se guarda en el ítem: se deriva de `categories.parent_id`. Un
# ítem es "hijo" cuando el presupuesto también tiene un ítem para la categoría
# padre de la suya. Así no puede quedar desincronizado si la categoría cambia
# de padre o se fusiona.
#
# El tope del padre se ADVIERTE, no se bloquea (regla de negocio 7).


def effective_parents[K: Hashable](category_parent: Mapping[K, K | None]) -> dict[K, K | None]:
    """`category_parent`: categoría presupuestada -> su `parent_id` (o None).
    Devuelve el mismo mapa pero con None donde el padre NO tiene ítem propio en
    el presupuesto — ese ítem queda como raíz."""
    return {
        category: (parent if parent in category_parent else None)
        for category, parent in category_parent.items()
    }


def rollup_spent[K: Hashable](
    category: K, child_categories: Sequence[K], spent_by_category: Mapping[K | None, int]
) -> int:
    """Consumo de un ítem: su categoría más las categorías hijas. Cada
    transacción tiene UNA categoría, así que no hay doble conteo aunque un hijo
    tenga además su propio ítem."""
    return spent_by_category.get(category, 0) + sum(
        spent_by_category.get(child, 0) for child in child_categories
    )


def children_excess(parent_cents: int, children_cents: Sequence[int]) -> int:
    """Cuánto se pasan los hijos del tope del padre (0 si caben)."""
    return max(0, sum(children_cents) - parent_cents)


@dataclass(frozen=True, slots=True)
class HierarchySummary[K: Hashable]:
    children_budgeted: dict[K, int]  # categoría padre -> suma de sus hijos
    children_excess: dict[K, int]  # categoría padre -> exceso (solo si > 0)
    root_total_cents: int  # suma de ítems raíz: lo que de verdad se presupuesta


def summarize_hierarchy[K: Hashable](
    budgeted: Mapping[K, int], parent_of: Mapping[K, K | None]
) -> HierarchySummary[K]:
    """`parent_of` ya viene de `effective_parents`. El total presupuestado cuenta
    solo las raíces: los hijos son un reparto DENTRO del tope del padre, sumarlos
    duplicaría plata."""
    children: dict[K, list[int]] = {}
    root_total = 0
    for category, amount in budgeted.items():
        parent = parent_of.get(category)
        if parent is None:
            root_total += amount
        else:
            children.setdefault(parent, []).append(amount)

    return HierarchySummary(
        children_budgeted={parent: sum(cents) for parent, cents in children.items()},
        children_excess={
            parent: excess
            for parent, cents in children.items()
            if (excess := children_excess(budgeted[parent], cents)) > 0
        },
        root_total_cents=root_total,
    )
