from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

AccountType = Literal[
    "checking", "savings", "credit_card", "cash", "investment", "loan", "digital_wallet"
]


class AccountCreate(BaseModel):
    # El id lo genera el cliente (UUIDv7) — CLAUDE.md: el servidor nunca
    # asigna IDs de entidades de dominio.
    id: UUID
    name: str = Field(min_length=1, max_length=255)
    type: AccountType
    currency: str = Field(default="GTQ", min_length=3, max_length=3)
    institution: str | None = None
    last_four: str | None = Field(default=None, max_length=4)
    initial_balance_cents: int = 0
    color: str | None = None
    icon: str | None = None
    sort_order: int = 0
    credit_limit_cents: int | None = None
    statement_day: int | None = Field(default=None, ge=1, le=31)
    payment_due_day: int | None = Field(default=None, ge=1, le=31)
    interest_rate: Decimal | None = None


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    institution: str | None = None
    last_four: str | None = Field(default=None, max_length=4)
    color: str | None = None
    icon: str | None = None
    sort_order: int | None = None
    credit_limit_cents: int | None = None
    statement_day: int | None = Field(default=None, ge=1, le=31)
    payment_due_day: int | None = Field(default=None, ge=1, le=31)
    interest_rate: Decimal | None = None


class AccountAdjustRequest(BaseModel):
    real_balance_cents: int
    note: str = Field(min_length=1, max_length=500)


class AccountOut(BaseModel):
    id: UUID
    name: str
    type: AccountType
    currency: str
    institution: str | None
    last_four: str | None
    initial_balance_cents: int
    current_balance_cents: int
    is_archived: bool
    color: str | None
    icon: str | None
    sort_order: int
    credit_limit_cents: int | None
    statement_day: int | None
    payment_due_day: int | None
    interest_rate: Decimal | None
    balance_recalculated_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
