"""Próxima ocurrencia de una regla recurrente (suscripciones, alquiler,
colegiatura, seguros anuales). El caso que rompe implementaciones
ingenuas: fin de mes — día 31 en febrero (PLAN-backend §11, test
obligatorio)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from domain.dates import add_months_clamped

Frequency = Literal["daily", "weekly", "monthly", "quarterly", "yearly"]

_MONTHS_PER_UNIT: dict[Frequency, int] = {"monthly": 1, "quarterly": 3, "yearly": 12}

# Ocurrencias por mes, para normalizar cualquier frecuencia a "costo mensual".
# 4.345 = promedio de semanas por mes (52 / 12).
_OCCURRENCES_PER_MONTH: dict[Frequency, Decimal] = {
    "daily": Decimal(30),
    "weekly": Decimal("4.345"),
    "monthly": Decimal(1),
    "quarterly": Decimal(1) / 3,
    "yearly": Decimal(1) / 12,
}


class RecurrenceError(ValueError):
    pass


def next_occurrence(current: date, frequency: Frequency, interval: int = 1) -> date:
    if interval <= 0:
        raise RecurrenceError("interval debe ser mayor a 0")
    if frequency == "daily":
        return current + timedelta(days=interval)
    if frequency == "weekly":
        return current + timedelta(weeks=interval)
    if frequency in _MONTHS_PER_UNIT:
        return add_months_clamped(current, interval * _MONTHS_PER_UNIT[frequency])
    raise RecurrenceError(f"frequency desconocida: {frequency}")


def expand_occurrences(start: date, frequency: Frequency, interval: int, until: date) -> list[date]:
    """Todas las ocurrencias desde `start` (inclusive) hasta `until` (inclusive).
    Usado para generar transacciones atrasadas si el job no corrió unos días."""
    if start > until:
        return []
    occurrences = [start]
    current = start
    while True:
        current = next_occurrence(current, frequency, interval)
        if current > until:
            return occurrences
        occurrences.append(current)


def detect_price_increase(previous_amount_cents: int, new_amount_cents: int) -> bool:
    """Caso de suscripciones: "⚠️ Subió de Q79 a Q89"."""
    return new_amount_cents > previous_amount_cents


def monthly_equivalent_cents(amount_cents: int, frequency: Frequency, interval: int) -> int:
    """Normaliza cualquier frecuencia a "cuánto cuesta por mes" — lo que
    hace comparables una suscripción semanal y una anual."""
    if interval <= 0:
        raise RecurrenceError("interval debe ser mayor a 0")
    occurrences = _OCCURRENCES_PER_MONTH[frequency] / interval
    value = (Decimal(amount_cents) * occurrences).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(value)
