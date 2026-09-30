"""Un gasto o un ingreso no pueden existir sin categoría.

Aplica a los movimientos de dinero que entran o salen del usuario: gastos e
ingresos. Las transferencias entre cuentas propias no la llevan. Vale para todo
lo que termina siendo un gasto o un ingreso: la transacción, la plantilla, la
regla recurrente y el plan de cuotas.

Los ingresos que crea el propio sistema traen la suya: un cobro de "Me deben"
usa la categoría reservada "Cobros" y un reembolso hereda la del gasto original.
"""

from __future__ import annotations

EXPENSE = "expense"
INCOME = "income"
_REQUIRE_CATEGORY = frozenset({EXPENSE, INCOME})

# Nombre de la categoría de sistema que recibe los gastos que ya existían sin una
# cuando se aplicó esta regla (`scripts/backfill_uncategorized.py`).
UNCATEGORIZED_CATEGORY_NAME = "Sin categoría"


def requires_category(kind: str) -> bool:
    """True si un movimiento de este tipo debe llevar categoría."""
    return kind in _REQUIRE_CATEGORY


def is_missing_required_category(kind: str, category_id: object | None) -> bool:
    """True si es un gasto o un ingreso y no trae categoría."""
    return requires_category(kind) and category_id is None
