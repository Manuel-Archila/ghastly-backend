"""Derivación de saldos.

Los saldos NO se editan a mano — se derivan sumando el historial de
transacciones sobre el saldo inicial. `current_balance_cents` en la DB es
solo una caché de este cálculo, recalculada en la misma transacción de DB
que escribe el movimiento (CLAUDE.md regla 6).

Nota de diseño (no explícita en PLAN-backend, necesaria para que el saldo
de una tarjeta tenga sentido): las cuentas de tipo `credit_card` y `loan`
son PASIVOS. Un gasto contra una tarjeta AUMENTA lo que se debe; un pago
(transferencia entrante) lo REDUCE. Es el espejo de una cuenta normal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AccountType = Literal[
    "checking", "savings", "credit_card", "cash", "investment", "loan", "digital_wallet"
]
TransactionKind = Literal["expense", "income", "transfer"]
TransferDirection = Literal["in", "out"] | None

# Cuentas cuyo saldo representa una deuda, no un activo.
LIABILITY_ACCOUNT_TYPES: frozenset[str] = frozenset({"credit_card", "loan"})


class BalanceError(ValueError):
    """Dato de entrada inconsistente para calcular un saldo."""


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """La porción mínima de una transacción que hace falta para el saldo.

    `amount_cents` siempre positivo, igual que en la tabla `transactions`
    (CLAUDE.md). El signo lo decide `signed_delta`, en ningún otro lado.
    """

    kind: TransactionKind
    amount_cents: int
    transfer_direction: TransferDirection = None


def signed_delta(entry: LedgerEntry, account_type: AccountType) -> int:
    """El único lugar que decide el signo de un movimiento sobre un saldo."""
    if entry.amount_cents < 0:
        raise BalanceError("amount_cents debe ser positivo; el signo lo da esta función")

    is_liability = account_type in LIABILITY_ACCOUNT_TYPES

    if entry.kind == "income":
        # Un ingreso normal suma. Sobre una tarjeta (p. ej. un reembolso
        # acreditado directamente a ella) reduce lo que se debe.
        return -entry.amount_cents if is_liability else entry.amount_cents

    if entry.kind == "expense":
        return entry.amount_cents if is_liability else -entry.amount_cents

    if entry.kind == "transfer":
        if entry.transfer_direction not in ("in", "out"):
            raise BalanceError("una transferencia necesita transfer_direction 'in' u 'out'")
        incoming = entry.transfer_direction == "in"
        if is_liability:
            # Pagar la tarjeta es dinero que ENTRA a ella y reduce la deuda.
            return -entry.amount_cents if incoming else entry.amount_cents
        return entry.amount_cents if incoming else -entry.amount_cents

    raise BalanceError(f"kind desconocido: {entry.kind}")


def compute_balance(
    initial_balance_cents: int,
    entries: list[LedgerEntry],
    account_type: AccountType,
) -> int:
    """Recalcula el saldo completo desde el saldo inicial + todo el historial.

    `POST /accounts/{id}/recalculate` es literalmente esta función."""
    balance = initial_balance_cents
    for entry in entries:
        balance += signed_delta(entry, account_type)
    return balance


def resolve_adjustment(diff_cents: int, account_type: AccountType) -> tuple[TransactionKind, int]:
    """Caso de negocio 9 (ajuste de saldo): qué `kind` y monto hay que registrar
    para que el saldo derivado pase a ser el saldo real, sin nunca tocar
    `current_balance_cents` a mano.

    Reusa `signed_delta` en vez de repetir la inversión de signo de pasivos:
    prueba ambos `kind` y se queda con el que produce exactamente `diff_cents`.
    """
    if diff_cents == 0:
        raise BalanceError("no hay diferencia que ajustar")
    amount = abs(diff_cents)
    for kind in ("income", "expense"):
        entry = LedgerEntry(kind=kind, amount_cents=amount)
        if signed_delta(entry, account_type) == diff_cents:
            return kind, amount
    raise AssertionError("inalcanzable: income y expense son las únicas opciones")
