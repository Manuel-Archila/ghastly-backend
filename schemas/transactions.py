from __future__ import annotations

from datetime import date as date_
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

TransactionKind = Literal["expense", "income", "transfer"]


class TransactionCreate(BaseModel):
    id: UUID  # generado por el cliente (UUIDv7)
    account_id: UUID
    category_id: UUID | None = None
    kind: Literal["expense", "income"]  # las transferencias solo se crean vía /transfer
    amount_cents: int = Field(gt=0)
    currency: str = Field(default="GTQ", min_length=3, max_length=3)
    fx_rate: Decimal | None = None
    date: date_
    description: str | None = Field(default=None, max_length=500)
    merchant: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    is_tax_relevant: bool = False
    is_extraordinary: bool = False
    tags: list[str] = Field(default_factory=list)
    # Si viene, es una plantilla propia (transaction_templates) que se marca
    # usada — no se persiste en la transacción, es solo la señal para
    # incrementar use_count/last_used_at (PLAN-backend.md §8).
    template_id: UUID | None = None


class TransactionUpdate(BaseModel):
    category_id: UUID | None = None
    date: date_ | None = None
    description: str | None = Field(default=None, max_length=500)
    merchant: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    is_reconciled: bool | None = None
    is_tax_relevant: bool | None = None
    is_extraordinary: bool | None = None
    tags: list[str] | None = None


class TransferCreate(BaseModel):
    # Dos ids, uno por cada fila del par (el cliente ya los generó offline).
    out_transaction_id: UUID
    in_transaction_id: UUID
    from_account_id: UUID
    to_account_id: UUID
    amount_cents: int = Field(gt=0)
    date: date_
    description: str | None = Field(default=None, max_length=500)
    notes: str | None = None


class RefundCreate(BaseModel):
    id: UUID
    amount_cents: int | None = Field(default=None, gt=0)  # default: el total del gasto original
    date: date_ | None = None
    notes: str | None = None
    fx_rate: Decimal | None = None  # requerida si el gasto original no es GTQ (caso 4)


class BulkCategorizeRequest(BaseModel):
    ids: list[UUID]
    category_id: UUID


class TransactionOut(BaseModel):
    id: UUID
    account_id: UUID
    category_id: UUID | None
    kind: TransactionKind
    amount_cents: int
    currency: str
    fx_rate: Decimal | None
    base_amount_cents: int | None
    date: date_
    description: str | None
    merchant: str | None
    notes: str | None
    transfer_group_id: UUID | None
    transfer_direction: Literal["in", "out"] | None
    refund_of_id: UUID | None
    installment_id: UUID | None = None
    recurring_rule_id: UUID | None = None
    receivable_id: UUID | None = None
    receipt_key: str | None = None
    is_reconciled: bool
    is_tax_relevant: bool
    is_extraordinary: bool
    affects_closed_period: bool
    tags: list[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DuplicateWarning(BaseModel):
    code: Literal["POSSIBLE_DUPLICATE"] = "POSSIBLE_DUPLICATE"
    transaction_id: UUID


class TransactionCreateResult(BaseModel):
    transaction: TransactionOut
    warning: DuplicateWarning | None = None


class TransactionStats(BaseModel):
    count: int
    total_income_cents: int
    total_expense_cents: int
    net_cents: int


class TransactionListOut(BaseModel):
    items: list[TransactionOut]
    next_cursor: str | None = None
