from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

EntityType = Literal["account", "category", "transaction"]
Op = Literal["upsert", "delete"]


class SyncChangeOut(BaseModel):
    entity_type: str
    entity_id: UUID
    op: str
    payload: dict[str, Any]
    server_seq: int


class SyncPullOut(BaseModel):
    changes: list[SyncChangeOut]
    next_seq: int
    has_more: bool


class SyncMutationIn(BaseModel):
    client_mutation_id: UUID
    entity_type: EntityType
    entity_id: UUID
    op: Op
    payload: dict[str, Any] = Field(default_factory=dict)
    client_updated_at: datetime


class SyncPushRequest(BaseModel):
    device_id: UUID
    mutations: list[SyncMutationIn]


class SyncConflictOut(BaseModel):
    client_mutation_id: UUID
    entity_id: UUID
    reason: str
    server_payload: dict[str, Any] | None = None


class SyncPushResult(BaseModel):
    applied: list[UUID]
    conflicts: list[SyncConflictOut]
    next_seq: int


class SyncStatusOut(BaseModel):
    server_seq: int
