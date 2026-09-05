from __future__ import annotations

from datetime import date as date_
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Frequency = Literal["daily", "weekly", "monthly", "quarterly", "yearly"]


class RecurringRuleCreate(BaseModel):
    id: UUID
    account_id: UUID
    category_id: UUID | None = None
    kind: Literal["expense", "income"]
    name: str = Field(min_length=1, max_length=255)
    amount_cents: int = Field(gt=0)
    currency: str = Field(default="GTQ", min_length=3, max_length=3)
    frequency: Frequency
    interval: int = Field(default=1, gt=0)
    next_due_date: date_
    end_date: date_ | None = None
    auto_create: bool = True
    reminder_days_before: int = 1
    is_extraordinary: bool = False


class RecurringRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category_id: UUID | None = None
    amount_cents: int | None = Field(default=None, gt=0)
    end_date: date_ | None = None
    auto_create: bool | None = None
    reminder_days_before: int | None = None


class RecurringConfirmRequest(BaseModel):
    id: UUID  # id de la transacción que se crea
    date: date_ | None = None


class RecurringRuleOut(BaseModel):
    id: UUID
    account_id: UUID
    category_id: UUID | None
    kind: Literal["expense", "income"]
    name: str
    amount_cents: int
    currency: str
    frequency: Frequency
    interval: int
    next_due_date: date_
    end_date: date_ | None
    auto_create: bool
    reminder_days_before: int
    status: Literal["active", "paused", "ended"]
    last_generated_at: datetime | None
    last_amount_cents: int | None
    is_extraordinary: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SubscriptionSummaryItem(BaseModel):
    rule_id: UUID
    name: str
    amount_cents: int
    monthly_equivalent_cents: int
    frequency: Frequency
    next_due_date: date_
    price_increased: bool
    cancel_candidate: bool


class SubscriptionsSummaryOut(BaseModel):
    total_monthly_cents: int
    total_annualized_cents: int
    items: list[SubscriptionSummaryItem]
