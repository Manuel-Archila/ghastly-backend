"""Job de las 20:00: alertas de presupuesto al 80%/100%.

Complementa el gancho "tras cada escritura" (`budget_service.check_alerts_for_category`,
llamado desde `transaction_service.create_transaction`/`update_transaction`):
cubre los presupuestos que no tuvieron ningún movimiento ese día pero que
igual siguen sobre el umbral (p. ej. si el usuario no registró nada hoy).
"""

from __future__ import annotations

import structlog
from sqlalchemy import select

from services import budget_service
from services.budget_service import BUDGET_ALERT_THRESHOLDS
from storage.db import get_session_factory
from storage.models.budget import Budget

logger = structlog.get_logger("jobs.check_budget_alerts")


async def run() -> None:
    session_factory = get_session_factory()
    alerts = 0

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
            for item in current.items:
                threshold = next(
                    (t for t in BUDGET_ALERT_THRESHOLDS if item.percent_consumed >= t), None
                )
                if threshold is not None:
                    logger.info(
                        "budget_alert",
                        user_id=str(budget.user_id),
                        category_id=str(item.category_id),
                        percent_consumed=item.percent_consumed,
                        threshold=threshold,
                    )
                    alerts += 1

    logger.info("check_budget_alerts_done", budgets=len(budgets), alerts=alerts)
