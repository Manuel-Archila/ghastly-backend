from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

CategoryKind = Literal["expense", "income"]


class CategoryCreate(BaseModel):
    id: UUID
    name: str = Field(min_length=1, max_length=255)
    kind: CategoryKind
    parent_id: UUID | None = None
    icon: str | None = None
    color: str | None = None
    is_tax_deductible: bool = False
    sort_order: int = 0


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: UUID | None = None
    icon: str | None = None
    color: str | None = None
    is_tax_deductible: bool | None = None
    sort_order: int | None = None


class CategoryMergeRequest(BaseModel):
    into_id: UUID


class CategoryOut(BaseModel):
    id: UUID
    name: str
    kind: CategoryKind
    parent_id: UUID | None
    icon: str | None
    color: str | None
    is_archived: bool
    is_tax_deductible: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CategoryTreeOut(CategoryOut):
    children: list[CategoryOut] = Field(default_factory=list)
