"""Job de las 20:00: alertas de presupuesto al 80%/100%.

Solo la corrida programada está implementada acá. El gancho "tras cada
escritura" (PLAN-backend §9 — revisar el instante en que una transacción
cruza el umbral) requeriría engancharse a `transaction_service.create_transaction`
y no se hizo en este pase; queda documentado como pendiente."""

from __future__ import annotations

import structlog
from sqlalchemy import select

from services import budget_service
from storage.db import get_session_factory
from storage.models.budget import Budget

logger = structlog.get_logger("jobs.check_budget_alerts")

THRESHOLDS = (100, 80)  # se evalúa de mayor a menor; el primero que aplique gana


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
                threshold = next((t for t in THRESHOLDS if item.percent_consumed >= t), None)
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
