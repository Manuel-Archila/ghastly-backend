"""Separación capital/interés de un pago (deudas, cuotas con interés).

PLAN-backend §6: "Separar en cada pago el capital del interés. El interés
es gasto; el capital es reducción de pasivo." El residuo de redondeo lo
absorbe el ÚLTIMO pago, igual que `domain/money.py` — el saldo debe
terminar en exactamente cero, nunca en Q0.03 sueltos.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from domain.money import Money


class AmortizationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AmortizationEntry:
    number: int
    payment_cents: int
    principal_cents: int
    interest_cents: int
    remaining_balance_cents: int


def _round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fixed_payment_cents(principal_cents: int, monthly_rate: Decimal, num_payments: int) -> int:
    """La cuota fija de una amortización francesa a tasa `monthly_rate`."""
    if num_payments <= 0:
        raise AmortizationError("num_payments debe ser mayor a 0")
    if monthly_rate == 0:
        return -(-principal_cents // num_payments)  # techo: nunca falta para pagar
    p = Decimal(principal_cents)
    r = monthly_rate
    payment = p * r / (1 - (1 + r) ** -num_payments)
    return _round_half_up(payment)


def amortize(
    principal_cents: int, monthly_rate: Decimal, num_payments: int
) -> list[AmortizationEntry]:
    if principal_cents <= 0:
        raise AmortizationError("principal_cents debe ser mayor a 0")
    if num_payments <= 0:
        raise AmortizationError("num_payments debe ser mayor a 0")

    if monthly_rate == 0:
        shares = Money(principal_cents).allocate(num_payments)
        entries = []
        balance = principal_cents
        for i, share in enumerate(shares, start=1):
            balance -= share.cents
            entries.append(AmortizationEntry(i, share.cents, share.cents, 0, balance))
        return entries

    payment = fixed_payment_cents(principal_cents, monthly_rate, num_payments)
    entries = []
    balance = principal_cents
    for number in range(1, num_payments + 1):
        interest = _round_half_up(Decimal(balance) * monthly_rate)
        if number == num_payments:
            # El último pago absorbe el residuo: el saldo debe llegar a cero exacto.
            principal = balance
            entries.append(AmortizationEntry(number, principal + interest, principal, interest, 0))
            break
        principal = payment - interest
        balance -= principal
        entries.append(AmortizationEntry(number, payment, principal, interest, balance))
    return entries
