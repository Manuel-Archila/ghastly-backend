from __future__ import annotations

from pydantic import BaseModel


class InfoOut(BaseModel):
    version: str
    # None si la tabla de pyway está vacía (no debería pasar en un
    # despliegue real, pero un entorno recién creado sin migrar sí puede
    # darse en desarrollo).
    migration_version: str | None
    migration_name: str | None
