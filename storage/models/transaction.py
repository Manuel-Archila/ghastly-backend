from __future__ import annotations

import uuid
from datetime import date as date_
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ARRAY, BigInteger, Boolean, Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from storage.models.base import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False, index=True
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id")
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # expense | income | transfer
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="GTQ")
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    base_amount_cents: Mapped[int | None] = mapped_column(BigInteger)
    date: Mapped[date_] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    merchant: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)

    transfer_group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    transfer_direction: Mapped[str | None] = mapped_column(String(3))  # in | out
    refund_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id")
    )
    installment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("installments.id")
    )
    recurring_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recurring_rules.id")
    )
    receivable_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("receivables.id")
    )

    is_reconciled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_tax_relevant: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_extraordinary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    affects_closed_period: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    receipt_key: Mapped[str | None] = mapped_column(String(500))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, server_default="{}")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    server_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
