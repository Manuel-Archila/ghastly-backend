"""Horas de silencio de `notification_preferences` (PLAN-backend.md §5):
mientras estén activas, `services/push_service.py` no manda push. Función
pura — la hora "actual" la resuelve el llamador con
`core/timezone.now_in_business_tz()`.
"""

from __future__ import annotations

from datetime import time


def is_within_quiet_hours(current: time, start: time | None, end: time | None) -> bool:
    """Sin `start`/`end` configurados, nunca hay silencio. Si `start > end`
    la ventana cruza medianoche (p. ej. 22:00–07:00): en ese caso está
    "dentro" cuando la hora actual es mayor o igual al inicio, O menor al
    fin — el complemento de estar despierto entre `end` y `start`."""
    if start is None or end is None:
        return False
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end
