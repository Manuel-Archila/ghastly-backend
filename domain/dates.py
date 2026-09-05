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
