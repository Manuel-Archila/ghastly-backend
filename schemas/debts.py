from __future__ import annotations

from datetime import date as date_
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from schemas.common import PatchModel

DebtType = Literal["personal_loan", "mortgage", "auto_loan", "student_loan", "other"]


class DebtCreate(BaseModel):
    id: UUID
    name: str = Field(min_length=1, max_length=255)
    type: DebtType = "other"
    principal_cents: int = Field(gt=0)
    monthly_interest_rate: Decimal = Decimal(0)
    monthly_payment_cents: int | None = Field(default=None, gt=0)
    start_date: date_
    term_months: int | None = Field(default=None, gt=0)
    linked_account_id: UUID | None = None


class DebtUpdate(PatchModel):
    non_nullable = frozenset({"name"})
    name: str | None = Field(default=None, min_length=1, max_length=255)
    monthly_payment_cents: int | None = Field(default=None, gt=0)
    term_months: int | None = Field(default=None, gt=0)


class DebtPaymentCreate(BaseModel):
    id: UUID  # id del registro DebtPayment
    from_account_id: UUID
    date: date_
    total_cents: int = Field(gt=0)
    fees_cents: int = 0
    # Opcional: si no se envían, se calculan a partir de balance * tasa mensual.
    principal_cents: int | None = Field(default=None, ge=0)
    interest_cents: int | None = Field(default=None, ge=0)
    # El service crea hasta 2 movimientos de dinero además del DebtPayment
    # (interés y capital, o interés y transferencia). Cada uno necesita su
    # propio id generado por el cliente — igual que TransferCreate, se piden
    # todos por adelantado y el que no aplica (p. ej. no hay interés ese mes)
    # simplemente no se usa.
    interest_transaction_id: UUID
    principal_transaction_id: UUID
    principal_transfer_out_id: UUID
    principal_transfer_in_id: UUID


class DebtOut(BaseModel):
    id: UUID
    name: str
    type: DebtType
    principal_cents: int
    balance_cents: int
    monthly_interest_rate: Decimal
    monthly_payment_cents: int | None
    start_date: date_
    term_months: int | None
    linked_account_id: UUID | None
    status: Literal["active", "paid_off"]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DebtPaymentOut(BaseModel):
    id: UUID
    debt_id: UUID
    date: date_
    total_cents: int
    principal_cents: int
    interest_cents: int
    fees_cents: int
    transaction_id: UUID | None

    model_config = {"from_attributes": True}


class AmortizationRow(BaseModel):
    number: int
    payment_cents: int
    principal_cents: int
    interest_cents: int
    remaining_balance_cents: int


class AmortizationOut(BaseModel):
    rows: list[AmortizationRow]
