from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from schemas.common import PatchModel


class TransactionTemplateCreate(BaseModel):
    id: UUID
    name: str = Field(min_length=1, max_length=255)
    account_id: UUID
    category_id: UUID | None = None
    kind: Literal["expense", "income"]
    amount_cents: int = Field(gt=0)
    description: str | None = Field(default=None, max_length=500)


class TransactionTemplateUpdate(PatchModel):
    non_nullable = frozenset({"name", "account_id", "kind", "amount_cents"})
    name: str | None = Field(default=None, min_length=1, max_length=255)
    account_id: UUID | None = None
    category_id: UUID | None = None  # null explícito = quitar la categoría
    kind: Literal["expense", "income"] | None = None
    amount_cents: int | None = Field(default=None, gt=0)
    description: str | None = Field(default=None, max_length=500)


class TransactionTemplateOut(BaseModel):
    id: UUID
    name: str
    account_id: UUID
    category_id: UUID | None
    kind: Literal["expense", "income"]
    amount_cents: int
    description: str | None
    use_count: int
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
