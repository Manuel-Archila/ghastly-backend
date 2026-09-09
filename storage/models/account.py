from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from storage.models.base import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="GTQ")
    institution: Mapped[str | None] = mapped_column(String(255))
    last_four: Mapped[str | None] = mapped_column(String(4))
    initial_balance_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    current_balance_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    color: Mapped[str | None] = mapped_column(String(20))
    icon: Mapped[str | None] = mapped_column(String(50))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Solo aplican a type == "credit_card".
    credit_limit_cents: Mapped[int | None] = mapped_column(BigInteger)
    statement_day: Mapped[int | None] = mapped_column(SmallInteger)
    payment_due_day: Mapped[int | None] = mapped_column(SmallInteger)
    interest_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    minimum_payment_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    balance_recalculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    server_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
