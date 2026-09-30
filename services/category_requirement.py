"""Aplica la regla "un gasto no puede existir sin categoría" (`domain/category_rule.py`)."""

from __future__ import annotations

from uuid import UUID

from core.errors import ValidationAppError
from domain.category_rule import is_missing_required_category


def ensure_category_present(kind: str, category_id: UUID | None) -> None:
    """Rechaza (422 `CATEGORY_REQUIRED`) un gasto sin categoría.

    Los ingresos y las transferencias no la necesitan. El cliente decide por el
    `code`, nunca por el mensaje.
    """
    if is_missing_required_category(kind, category_id):
        raise ValidationAppError(
            "Un gasto necesita una categoría.",
            field="category_id",
            code="CATEGORY_REQUIRED",
        )
