"""Job mensual (día 1, 06:00): detecta categorías con gasto anómalo en el mes
que acaba de cerrar, contra el promedio de los 3 anteriores (PLAN-backend.md
§9). La detección vive en `domain/anomalies.py` — es la misma lógica que usa
GET /reports/anomalies on-demand, acá corrida para todos los usuarios.

Sin PII en logs (CLAUDE.md): se registra el id de categoría y el % de
aumento, nunca los montos — el push sí lleva el nombre de la categoría
(igual que cualquier notificación en el propio teléfono del usuario)."""

from __future__ import annotations

from datetime import date

import structlog
from sqlalchemy import select

from core.timezone import today_in_business_tz
from services import push_service, report_service
from storage.db import get_session_factory
from storage.models.user import User

logger = structlog.get_logger("jobs.detect_anomalies")


def _previous_month_str(today: date) -> str:
    if today.month == 1:
        return f"{today.year - 1:04d}-12"
    return f"{today.year:04d}-{today.month - 1:02d}"


async def run() -> None:
    month = _previous_month_str(today_in_business_tz())
    session_factory = get_session_factory()
    anomalies_found = 0

    async with session_factory() as db:
        user_ids = list((await db.execute(select(User.id))).scalars().all())

        for user_id in user_ids:
            try:
                result = await report_service.get_anomalies(db, user_id, month)
            except Exception:
                logger.error("detect_anomalies_user_failed", user_id=str(user_id))
                continue
            for item in result.items:
                logger.info(
                    "spending_anomaly",
                    user_id=str(user_id),
                    category_id=str(item.category_id),
                    month=month,
                    percent_increase=item.percent_increase,
                )
                anomalies_found += 1
                await push_service.notify_spending_anomaly(
                    db, user_id, item.category_name, item.percent_increase
                )
        await db.commit()

    logger.info("detect_anomalies_done", users=len(user_ids), anomalies=anomalies_found)
