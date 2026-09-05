from __future__ import annotations

import uuid
from datetime import date as date_
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from storage.models.base import Base


class InstallmentPlan(Base):
    __tablename__ = "installment_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id")
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    merchant: Mapped[str | None] = mapped_column(String(255))
    total_amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    installments_count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_payment_date: Mapped[date_] = mapped_column(Date, nullable=False)
    monthly_interest_rate: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, server_default="0"
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


class Installment(Base):
    __tablename__ = "installments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("installment_plans.id"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    due_date: Mapped[date_] = mapped_column(Date, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    principal_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    interest_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id")
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    server_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
