from __future__ import annotations

import uuid
from datetime import date as date_
from datetime import datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from storage.models.base import Base


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_amount_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    target_date: Mapped[date_ | None] = mapped_column(Date)
    linked_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )
    icon: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="active")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    server_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


class GoalContribution(Base):
    __tablename__ = "goal_contributions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id"), nullable=False, index=True
    )
    date: Mapped[date_] = mapped_column(Date, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    server_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
