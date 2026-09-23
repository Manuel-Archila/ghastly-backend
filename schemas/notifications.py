from __future__ import annotations

from datetime import time

from pydantic import BaseModel

from schemas.common import PatchModel


class NotificationPreferencesOut(BaseModel):
    budget_alert_thresholds: list[int]
    due_reminder_days: int
    quiet_hours_start: time | None
    quiet_hours_end: time | None
    channels: list[str]

    model_config = {"from_attributes": True}


class NotificationPreferencesUpdate(PatchModel):
    """Todos opcionales — PATCH parcial, mismo estilo que `MeUpdateRequest`.
    La validación de reglas (rangos, unicidad, quiet_hours ambos o
    ninguno) vive en el service, no acá — mismo patrón que
    `receivable_service` (`ValidationAppError` con `code` estable)."""

    non_nullable = frozenset({"budget_alert_thresholds", "due_reminder_days", "channels"})

    budget_alert_thresholds: list[int] | None = None
    due_reminder_days: int | None = None
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    channels: list[str] | None = None
