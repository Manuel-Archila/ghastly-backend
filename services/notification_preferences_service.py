"""Singleton por usuario (PLAN-backend.md §5) — reemplaza los defaults que
`services/push_service.py`, `services/budget_service.py` y
`jobs/send_due_reminders.py` traían hardcodeados.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ValidationAppError
from schemas.notifications import NotificationPreferencesUpdate
from storage.models.notification import NotificationPreferences

ALLOWED_CHANNELS = {"push"}
MIN_THRESHOLD = 1
MAX_THRESHOLD = 100


async def get_or_create(db: AsyncSession, user_id: UUID) -> NotificationPreferences:
    prefs = await db.get(NotificationPreferences, user_id)
    if prefs is not None:
        return prefs
    prefs = NotificationPreferences(user_id=user_id)
    db.add(prefs)
    await db.commit()
    await db.refresh(prefs)
    return prefs


def _validate(data: dict[str, object]) -> None:
    if "budget_alert_thresholds" in data:
        thresholds = data["budget_alert_thresholds"]
        assert isinstance(thresholds, list)
        if not thresholds:
            raise ValidationAppError(
                "budget_alert_thresholds no puede estar vacío.", code="INVALID_THRESHOLDS"
            )
        if len(set(thresholds)) != len(thresholds):
            raise ValidationAppError(
                "budget_alert_thresholds no puede tener valores repetidos.",
                code="INVALID_THRESHOLDS",
            )
        if any(not (MIN_THRESHOLD <= t <= MAX_THRESHOLD) for t in thresholds):
            raise ValidationAppError(
                f"Los umbrales deben estar entre {MIN_THRESHOLD} y {MAX_THRESHOLD}.",
                code="INVALID_THRESHOLDS",
            )

    if "due_reminder_days" in data:
        days = data["due_reminder_days"]
        assert isinstance(days, int)
        if days < 0:
            raise ValidationAppError(
                "due_reminder_days no puede ser negativo.", code="INVALID_DUE_REMINDER_DAYS"
            )

    if "channels" in data:
        channels = data["channels"]
        assert isinstance(channels, list)
        invalid = set(channels) - ALLOWED_CHANNELS
        if invalid:
            raise ValidationAppError(
                f"Canal no soportado: {', '.join(sorted(invalid))}.", code="INVALID_CHANNEL"
            )


def _validate_quiet_hours(start: object, end: object) -> None:
    if (start is None) != (end is None):
        raise ValidationAppError(
            "quiet_hours_start y quiet_hours_end van juntos: los dos o ninguno.",
            code="INVALID_QUIET_HOURS",
        )


async def update(
    db: AsyncSession, user_id: UUID, patch: NotificationPreferencesUpdate
) -> NotificationPreferences:
    data = patch.model_dump(exclude_unset=True)
    _validate(data)

    prefs = await get_or_create(db, user_id)

    # Se valida contra el estado FINAL combinado (no solo lo que trae este
    # PATCH): mandar solo `quiet_hours_start` no debe poder dejar el otro
    # campo huérfano con el valor viejo en DB.
    final_start = data.get("quiet_hours_start", prefs.quiet_hours_start)
    final_end = data.get("quiet_hours_end", prefs.quiet_hours_end)
    if "quiet_hours_start" in data or "quiet_hours_end" in data:
        _validate_quiet_hours(final_start, final_end)

    for field, value in data.items():
        setattr(prefs, field, value)
    await db.commit()
    await db.refresh(prefs)
    return prefs
