"""Paginación por cursor (CLAUDE.md: nunca offset).

El cursor es opaco para el cliente: codifica el punto exacto (fecha, id)
donde se quedó la página anterior de `GET /transactions`, ordenado por
`(date DESC, id DESC)`.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date as date_
from uuid import UUID


class CursorError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Cursor:
    date: date_
    id: UUID

    def encode(self) -> str:
        raw = f"{self.date.isoformat()}|{self.id}"
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    @classmethod
    def decode(cls, value: str) -> Cursor:
        try:
            raw = base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")
            date_str, id_str = raw.split("|")
            return cls(date=date_.fromisoformat(date_str), id=UUID(id_str))
        except (ValueError, UnicodeDecodeError) as exc:
            raise CursorError("cursor inválido") from exc
