from __future__ import annotations

import uuid
from datetime import date as date_
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from storage.models.base import Base


class RecurringRule(Base):
    __tablename__ = "recurring_rules"

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
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # expense | income
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="GTQ")
    frequency: Mapped[str] = mapped_column(String(10), nullable=False)
    interval: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    next_due_date: Mapped[date_] = mapped_column(Date, nullable=False)
    end_date: Mapped[date_ | None] = mapped_column(Date)
    auto_create: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    reminder_days_before: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="active")
    last_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_amount_cents: Mapped[int | None] = mapped_column(BigInteger)
    price_history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    is_extraordinary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    server_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
