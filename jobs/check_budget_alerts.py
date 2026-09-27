"""Job de las 20:00: alertas de presupuesto sobre los umbrales configurados
en `notification_preferences.budget_alert_thresholds` (default 80/100).

Complementa el gancho "tras cada escritura" (`budget_service.check_alerts_for_category`,
llamado desde `transaction_service.create_transaction`/`update_transaction`):
cubre los presupuestos que no tuvieron ningún movimiento ese día pero que
igual siguen sobre el umbral (p. ej. si el usuario no registró nada hoy).
"""

from __future__ import annotations

import structlog
from sqlalchemy import select

from core.timezone import today_in_business_tz
from services import budget_service, notification_preferences_service, push_service
from storage.db import get_session_factory
from storage.models.budget import Budget

logger = structlog.get_logger("jobs.check_budget_alerts")


async def run() -> None:
    session_factory = get_session_factory()
    alerts = 0
    today = today_in_business_tz()
    month = f"{today.year:04d}-{today.month:02d}"

    async with session_factory() as db:
        result = await db.execute(
            select(Budget).where(Budget.is_active.is_(True), Budget.deleted_at.is_(None))
        )
        budgets = list(result.scalars().all())

        for budget in budgets:
            try:
                current = await budget_service.get_current(db, budget.user_id, None)
            except Exception:
                continue
            prefs = await notification_preferences_service.get_or_create(db, budget.user_id)
            thresholds = sorted(prefs.budget_alert_thresholds, reverse=True)
            for item in current.items:
                threshold = next((t for t in thresholds if item.percent_consumed >= t), None)
                if threshold is not None:
                    logger.info(
                        "budget_alert",
                        user_id=str(budget.user_id),
                        category_id=str(item.category_id),
                        percent_consumed=item.percent_consumed,
                        threshold=threshold,
                    )
                    alerts += 1
                    # `notify_budget_alert` se salta el envío si el gancho
                    # tras cada escritura ya avisó este umbral este mes
                    # (dedup vía `budget_alerts_sent`, PLAN de Fase 5).
                    await push_service.notify_budget_alert(
                        db,
                        budget.user_id,
                        item.category_id,
                        item.category_name,
                        month,
                        item.percent_consumed,
                        threshold,
                    )
        await db.commit()

    logger.info("check_budget_alerts_done", budgets=len(budgets), alerts=alerts)
