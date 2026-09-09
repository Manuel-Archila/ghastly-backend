"""Ciclo de tarjeta de crédito: próxima fecha de corte y de pago.

`statement_day` / `payment_due_day` son el día del mes (1-31, recortado si
el mes no lo tiene) que ya vive en `accounts`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from domain.dates import next_day_of_month


@dataclass(frozen=True, slots=True)
class CreditCycle:
    statement_date: date
    payment_due_date: date
    days_until_statement: int
    days_until_payment_due: int


def compute_current_cycle(today: date, statement_day: int, payment_due_day: int) -> CreditCycle:
    statement_date = next_day_of_month(today, statement_day, inclusive=True)
    payment_due_date = next_day_of_month(statement_date, payment_due_day, inclusive=False)
    return CreditCycle(
        statement_date=statement_date,
        payment_due_date=payment_due_date,
        days_until_statement=(statement_date - today).days,
        days_until_payment_due=(payment_due_date - today).days,
    )
