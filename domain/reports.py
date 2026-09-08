"""Agregaciones puras para /reports/dashboard: patrimonio neto, top
categorías y vista previa de vencimientos.

El service (`services/report_service.py`) junta los números desde la DB
(saldos de cuentas, deudas, sumas por categoría, listas de cuotas y
recurrentes) y se los pasa a este módulo — nada de esto toca SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from domain.balances import LIABILITY_ACCOUNT_TYPES, AccountType


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
class UpcomingSource:
    source_type: Literal["installment", "recurring"]
    source_id: UUID
    name: str
    due_date: date
    amount_cents: int


def build_upcoming_preview(sources: list[UpcomingSource], limit: int = 5) -> list[UpcomingSource]:
    """Une cuotas + recurrentes ya filtrados por ventana de días, ordena por
    fecha y recorta a `limit` — vista previa liviana; el calendario completo
    vive en GET /reports/upcoming (fuera de este alcance)."""

    def sort_key(source: UpcomingSource) -> tuple[date, str, str]:
        return source.due_date, source.source_type, source.name

    return sorted(sources, key=sort_key)[:limit]
