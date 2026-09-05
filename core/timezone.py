"""La zona de negocio la define el servidor, no el dispositivo (CLAUDE.md).

El corte de "mes" para presupuestos, cierres y jobs usa siempre esta zona.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = ZoneInfo("America/Guatemala")


def now_in_business_tz() -> datetime:
    return datetime.now(UTC).astimezone(BUSINESS_TIMEZONE)


def today_in_business_tz() -> date:
    return now_in_business_tz().date()
