"""Job mensual (día 1, 00:15): cierra el mes anterior de cada presupuesto activo."""

from __future__ import annotations

import structlog
from sqlalchemy import select

from core.errors import ConflictError
from services import budget_service
from storage.db import get_session_factory
from storage.models.budget import Budget

logger = structlog.get_logger("jobs.close_budget_periods")


async def run() -> None:
    session_factory = get_session_factory()
    closed = 0
    async with session_factory() as db:
        result = await db.execute(
            select(Budget).where(Budget.is_active.is_(True), Budget.deleted_at.is_(None))
        )
        budgets = list(result.scalars().all())

        for budget in budgets:
            try:
                await budget_service.close_period(db, budget.user_id, budget.id)
                closed += 1
            except ConflictError:
                # PERIOD_ALREADY_CLOSED: ya se cerró (corrida manual previa, p. ej.). No-op.
                await db.rollback()
            except Exception:
                await db.rollback()
                logger.error("close_budget_period_failed", budget_id=str(budget.id))

    logger.info("close_budget_periods_done", budgets=len(budgets), closed=closed)
