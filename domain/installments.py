"""Calendario de compras a cuotas (caso de negocio 6).

Crear el plan NO genera un gasto por el total — genera N `installments`
futuras. Solo la cuota del mes corriente impacta el presupuesto; el pasivo
total sale de sumar las no pagadas (eso lo hace el service, no acá).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from domain.amortization import amortize
from domain.dates import add_months_clamped


@dataclass(frozen=True, slots=True)
class InstallmentScheduleEntry:
    number: int
    due_date: date
    amount_cents: int
    principal_cents: int
    interest_cents: int


def generate_installment_schedule(
    total_amount_cents: int,
    installments_count: int,
    first_payment_date: date,
    monthly_interest_rate: Decimal = Decimal(0),
) -> list[InstallmentScheduleEntry]:
    entries = amortize(total_amount_cents, monthly_interest_rate, installments_count)
    return [
        InstallmentScheduleEntry(
            number=e.number,
            due_date=add_months_clamped(first_payment_date, e.number - 1),
            amount_cents=e.payment_cents,
            principal_cents=e.principal_cents,
            interest_cents=e.interest_cents,
        )
        for e in entries
    ]
