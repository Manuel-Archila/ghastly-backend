from __future__ import annotations

from datetime import date as date_
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class InstallmentPlanCreate(BaseModel):
    id: UUID
    account_id: UUID
    category_id: UUID | None = None
    description: str = Field(min_length=1, max_length=500)
    merchant: str | None = None
    total_amount_cents: int = Field(gt=0)
    installments_count: int = Field(gt=0)
    first_payment_date: date_
    monthly_interest_rate: Decimal = Decimal(0)
    # Uno por cuota, generados por el cliente (UUIDv7) para que /installments/{id}
    # tenga un id estable desde la vista previa hasta guardar.
    installment_ids: list[UUID]


class InstallmentPlanUpdate(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=500)
    merchant: str | None = None
    category_id: UUID | None = None


class InstallmentPayRequest(BaseModel):
    id: UUID  # id de la transacción que se crea
    date: date_ | None = None


class InstallmentOut(BaseModel):
    id: UUID
    plan_id: UUID
    number: int
    due_date: date_
    amount_cents: int
    principal_cents: int
    interest_cents: int
    paid_at: datetime | None
    transaction_id: UUID | None
    status: Literal["pending", "paid", "skipped"]

    model_config = {"from_attributes": True}


class InstallmentPlanOut(BaseModel):
    id: UUID
    account_id: UUID
    category_id: UUID | None
    description: str
    merchant: str | None
    total_amount_cents: int
    installments_count: int
    first_payment_date: date_
    monthly_interest_rate: Decimal
    status: Literal["active", "completed", "cancelled"]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class InstallmentPlanWithScheduleOut(InstallmentPlanOut):
    schedule: list[InstallmentOut]


class InstallmentLiabilityMonth(BaseModel):
    month: str  # 'YYYY-MM'
    amount_cents: int


class InstallmentLiabilityOut(BaseModel):
    total_pending_cents: int
    by_month: list[InstallmentLiabilityMonth]
