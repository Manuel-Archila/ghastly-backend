from __future__ import annotations

from datetime import date as date_
from decimal import Decimal

from sqlalchemy import Date, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from storage.models.base import Base


class FxRate(Base):
    """PK compuesta `(date, from_currency, to_currency)`. `rate` es la
    única tasa que se guarda como `NUMERIC` — no es dinero de una cuenta,
    es un factor de conversión (domain/money.py la consume como Decimal)."""

    __tablename__ = "fx_rates"

    date: Mapped[date_] = mapped_column(Date, primary_key=True)
    from_currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    to_currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # manual | api
