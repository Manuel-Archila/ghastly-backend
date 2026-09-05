from __future__ import annotations

import uuid
from datetime import date as date_
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from storage.models.base import Base


class Debt(Base):
    __tablename__ = "debts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="other")
    principal_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    monthly_interest_rate: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, server_default="0"
    )
    monthly_payment_cents: Mapped[int | None] = mapped_column(BigInteger)
    start_date: Mapped[date_] = mapped_column(Date, nullable=False)
    term_months: Mapped[int | None] = mapped_column(Integer)
    linked_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="active")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    server_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


class DebtPayment(Base):
    __tablename__ = "debt_payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    debt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("debts.id"), nullable=False, index=True
    )
    date: Mapped[date_] = mapped_column(Date, nullable=False)
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    principal_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    interest_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    fees_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    server_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
