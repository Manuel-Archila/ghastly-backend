"""Aritmética de dinero.

⚠️ Ninguna operación de dinero ocurre fuera de este módulo (CLAUDE.md).

Todo monto se representa como un entero en centavos (`BIGINT` en la DB,
`int` en Python). Nunca `float`, nunca `Decimal` como tipo de almacenamiento
— `Decimal` solo se usa aquí, internamente, para conversiones de tasa de
cambio, y el resultado siempre se vuelve a convertir a `int`.

Reglas de redondeo:
- Half-up.
- En un reparto (p. ej. el calendario de cuotas), el residuo de la
  división entera lo absorbe la ÚLTIMA fracción. Nunca se inventa ni se
  pierde un centavo.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CurrencyCode = str  # "GTQ" | "USD" | ...

_SYMBOLS: dict[str, str] = {"GTQ": "Q", "USD": "$"}


class MoneyError(ValueError):
    """Operación inválida sobre dinero: monedas distintas, monto no entero, etc."""


@dataclass(frozen=True, slots=True)
class Money:
    """Un monto inmutable, en centavos, en una moneda dada."""

    cents: int
    currency: CurrencyCode = "GTQ"

    def __post_init__(self) -> None:
        if not isinstance(self.cents, int) or isinstance(self.cents, bool):
            raise MoneyError(f"cents debe ser int, recibido {type(self.cents).__name__}")

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise MoneyError(
                f"no se pueden operar montos de distinta moneda: "
                f"{self.currency} vs {other.currency}"
            )

    def add(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.cents + other.cents, self.currency)

    def subtract(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.cents - other.cents, self.currency)

    def negate(self) -> Money:
        return Money(-self.cents, self.currency)

    def is_zero(self) -> bool:
        return self.cents == 0

    def is_negative(self) -> bool:
        return self.cents < 0

    def allocate(self, parts: int) -> list[Money]:
        """Reparte el monto en `parts` fracciones iguales.

        El residuo de la división entera lo absorbe la última fracción
        (caso de negocio: cuotas — PLAN-backend §6 regla 6).
        """
        if parts <= 0:
            raise MoneyError("parts debe ser mayor a 0")
        base = self.cents // parts
        remainder = self.cents - (base * parts)
        shares = [base] * parts
        shares[-1] += remainder
        return [Money(c, self.currency) for c in shares]

    def convert(self, rate: Decimal, to_currency: CurrencyCode) -> Money:
        """Convierte a otra moneda con una tasa fija ya congelada.

        `rate` es cuántas unidades de `to_currency` vale 1 unidad de
        `self.currency`. El llamador es responsable de congelar `rate`
        al momento de la transacción (PLAN-backend §6 regla 4) — esta
        función no sabe ni le importa de dónde salió la tasa.
        """
        converted = (Decimal(self.cents) * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return Money(int(converted), to_currency)

    def format(self) -> str:
        """Formato para mostrar al usuario, p. ej. `Q1,234.56` / `-$12.00`."""
        symbol = _SYMBOLS.get(self.currency, f"{self.currency} ")
        sign = "-" if self.cents < 0 else ""
        whole, frac = divmod(abs(self.cents), 100)
        return f"{sign}{symbol}{whole:,}.{frac:02d}"


def sum_money(amounts: list[Money], currency: CurrencyCode = "GTQ") -> Money:
    """Suma una lista de montos de la misma moneda."""
    total = Money(0, currency)
    for amount in amounts:
        total = total.add(amount)
    return total
