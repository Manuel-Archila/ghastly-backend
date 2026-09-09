from __future__ import annotations

from datetime import date as date_
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ReceivableCreate(BaseModel):
    id: UUID
    transaction_id: UUID
    counterparty: str = Field(min_length=1, max_length=255)
    amount_cents: int = Field(gt=0)


class ReceivableSettle(BaseModel):
    id: UUID  # id de la transacción de ingreso que se crea al liquidar
    account_id: UUID
    date: date_


class ReceivableOut(BaseModel):
    id: UUID
    transaction_id: UUID
    counterparty: str
    amount_cents: int
    settled_at: datetime | None
    settlement_transaction_id: UUID | None
    status: Literal["pending", "settled"]
    created_at: datetime
    updated_at: datetime
