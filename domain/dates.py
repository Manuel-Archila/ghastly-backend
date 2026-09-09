"""Aritmética de fechas de calendario compartida por cuotas, recurrencia y
ciclos de tarjeta. El caso que todas necesitan: sumar meses respetando que
no todos tienen 31 días (31 de enero + 1 mes = 28/29 de febrero, nunca 3 de
marzo)."""

from __future__ import annotations

import calendar
from datetime import date


def clamp_day(year: int, month: int, day: int) -> date:
    """El día `day` de `year-month`, recortado al último día si ese mes es más corto."""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def add_months_clamped(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    return clamp_day(year, month, start.day)


def next_day_of_month(reference: date, day: int, *, inclusive: bool) -> date:
    """La próxima vez que cae el día `day` del mes a partir de `reference`
    (hoy mismo si `inclusive` y hoy es ese día). Compartido por el ciclo de
    tarjeta (`domain/credit_cycle.py`) y el vencimiento mensual de deudas
    sin fecha de corte propia (`domain/reports.py`)."""
    candidate = clamp_day(reference.year, reference.month, day)
    if candidate > reference or (inclusive and candidate == reference):
        return candidate
    next_month = add_months_clamped(reference.replace(day=1), 1)
    return clamp_day(next_month.year, next_month.month, day)
