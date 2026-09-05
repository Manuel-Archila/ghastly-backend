from __future__ import annotations

import uuid
from datetime import date as date_
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from storage.models.base import Base


class Budget(Base):
    __tablename__ = "budgets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    period_type: Mapped[str] = mapped_column(String(10), nullable=False, server_default="monthly")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    rollover_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    global_limit_cents: Mapped[int | None] = mapped_column(BigInteger)
    income_basis: Mapped[str] = mapped_column(String(20), nullable=False, server_default="fixed")
    fixed_income_cents: Mapped[int | None] = mapped_column(BigInteger)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    server_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


class BudgetItem(Base):
    __tablename__ = "budget_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    budget_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budgets.id"), nullable=False, index=True
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id"), nullable=False
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rollover_enabled: Mapped[bool | None] = mapped_column(Boolean)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    server_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


class BudgetPeriod(Base):
    """Un mes cerrado (`close-period`). Congela el resultado; no se recalcula
    aunque después se edite una transacción de ese mes (queda `affects_closed_period`)."""

    __tablename__ = "budget_periods"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    budget_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budgets.id"), nullable=False, index=True
    )
    month: Mapped[str] = mapped_column(String(7), nullable=False)  # 'YYYY-MM'
    period_start: Mapped[date_] = mapped_column(Date, nullable=False)
    period_end: Mapped[date_] = mapped_column(Date, nullable=False)
    expected_income_cents: Mapped[int | None] = mapped_column(BigInteger)
    closed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    server_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


class BudgetPeriodItem(Base):
    __tablename__ = "budget_period_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    budget_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_periods.id"), nullable=False, index=True
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id"), nullable=False
    )
    budgeted_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rollover_in_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    spent_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rollover_out_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
