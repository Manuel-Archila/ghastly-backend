from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class ReorderRequest(BaseModel):
    ids: list[UUID]
