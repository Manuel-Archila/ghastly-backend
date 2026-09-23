from __future__ import annotations

from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ValidationInfo, field_validator


class ReorderRequest(BaseModel):
    ids: list[UUID]


class PatchModel(BaseModel):
    """Base de los schemas de PATCH parcial: campo omitido = no tocar.

    Un `null` explícito solo tiene sentido en campos que la columna acepta
    como NULL (`category_id`, `notes`...: "quitar el valor"). En los
    `NOT NULL` acabaría en un `IntegrityError` -> 500, así que cada schema
    declara en `non_nullable` cuáles rechazar con un 422 antes de tocar la DB.
    """

    non_nullable: ClassVar[frozenset[str]] = frozenset()

    @field_validator("*", mode="before")
    @classmethod
    def _reject_explicit_null(cls, value: Any, info: ValidationInfo) -> Any:
        # Con `mode="before"` solo corre para campos que vinieron en el
        # body: un campo omitido usa su default sin validarse.
        if value is None and info.field_name in cls.non_nullable:
            raise ValueError("Este campo no acepta null; omítelo para dejarlo igual.")
        return value
