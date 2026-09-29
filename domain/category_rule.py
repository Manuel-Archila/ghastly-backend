"""Un gasto no puede existir sin categoría.

Aplica a los gastos: los ingresos (un cobro, un reembolso) y las transferencias
pueden no tener categoría. Vale para todo lo que termina siendo un gasto: la
transacción, la plantilla, la regla recurrente y el plan de cuotas.
"""

from __future__ import annotations

EXPENSE = "expense"

# Nombre de la categoría de sistema que recibe los gastos que ya existían sin una
# cuando se aplicó esta regla (`scripts/backfill_uncategorized.py`).
UNCATEGORIZED_CATEGORY_NAME = "Sin categoría"


def requires_category(kind: str) -> bool:
    """True si un movimiento de este tipo debe llevar categoría."""
    return kind == EXPENSE


def is_missing_required_category(kind: str, category_id: object | None) -> bool:
    """True si es un gasto y no trae categoría."""
    return requires_category(kind) and category_id is None
