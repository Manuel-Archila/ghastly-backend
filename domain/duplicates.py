"""Detección de posibles duplicados (caso de negocio 10).

Advertir, no bloquear: la transacción se crea igual. Esto solo decide si
la respuesta de `POST /transactions` lleva `data.warning`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    account_id: UUID
    amount_cents: int
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ExistingTransaction:
    id: UUID
    account_id: UUID
    amount_cents: int
    occurred_at: datetime


def find_possible_duplicate(
    candidate: DuplicateCandidate,
    existing: list[ExistingTransaction],
    window_minutes: int = 5,
) -> ExistingTransaction | None:
    """Misma cuenta + mismo monto dentro de una ventana de tiempo -> posible duplicado.

    Devuelve la primera coincidencia (o `None`); el llamador decide qué hacer
    con ella (armar `data.warning`, nunca bloquear la creación)."""
    window = timedelta(minutes=window_minutes)
    for txn in existing:
        if txn.account_id != candidate.account_id:
            continue
        if txn.amount_cents != candidate.amount_cents:
            continue
        if abs(txn.occurred_at - candidate.occurred_at) <= window:
            return txn
    return None
