from __future__ import annotations

from datetime import date as date_
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from schemas.common import PatchModel


class GoalCreate(BaseModel):
    id: UUID
    name: str = Field(min_length=1, max_length=255)
    target_amount_cents: int = Field(gt=0)
    target_date: date_ | None = None
    linked_account_id: UUID | None = None
    icon: str | None = None


class GoalUpdate(PatchModel):
    non_nullable = frozenset({"name", "target_amount_cents"})
    name: str | None = Field(default=None, min_length=1, max_length=255)
    target_amount_cents: int | None = Field(default=None, gt=0)
    target_date: date_ | None = None
    icon: str | None = None


class GoalContributionCreate(BaseModel):
    id: UUID
    amount_cents: int = Field(gt=0)
    date: date_
    # Requeridos solo si la meta tiene linked_account_id y el aporte es
    # dinero real moviéndose (no un ajuste manual de progreso).
    from_account_id: UUID | None = None
    transfer_out_id: UUID | None = None
    transfer_in_id: UUID | None = None


class GoalOut(BaseModel):
    id: UUID
    name: str
    target_amount_cents: int
    current_amount_cents: int
    target_date: date_ | None
    linked_account_id: UUID | None
    icon: str | None
    status: Literal["active", "completed"]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GoalContributionOut(BaseModel):
    id: UUID
    goal_id: UUID
    date: date_
    amount_cents: int
    transaction_id: UUID | None

    model_config = {"from_attributes": True}
