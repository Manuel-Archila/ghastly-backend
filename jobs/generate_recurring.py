"""Job diario (00:05): genera transacciones de reglas con `auto_create=true`.

Idempotente por construcción: cada corrida solo toma reglas cuyo
`next_due_date` ya venció, y generarlas avanza esa fecha — correr dos
veces el mismo día no encuentra nada pendiente la segunda vez. Si el
servidor estuvo caído varios días, el `while` adentro pone al día todas
las ocurrencias atrasadas de una sola corrida.

Para `auto_create=false` no se genera nada — solo se avisa que hay una
confirmación pendiente.
"""

from __future__ import annotations

from uuid import uuid4

import structlog
from sqlalchemy import select

from core.timezone import today_in_business_tz
from schemas.recurring import RecurringConfirmRequest
from services import push_service, recurring_service
from storage.db import get_session_factory
from storage.models.recurring import RecurringRule

logger = structlog.get_logger("jobs.generate_recurring")


async def run() -> None:
    today = today_in_business_tz()
    session_factory = get_session_factory()
    generated = 0
    pending_confirmations = 0

    async with session_factory() as db:
        result = await db.execute(
            select(RecurringRule).where(
                RecurringRule.deleted_at.is_(None),
                RecurringRule.status == "active",
                RecurringRule.next_due_date <= today,
            )
        )
        rules = list(result.scalars().all())

        for rule in rules:
            if not rule.auto_create:
                logger.info(
                    "recurring_confirmation_pending",
                    rule_id=str(rule.id),
                    user_id=str(rule.user_id),
                    due_date=rule.next_due_date.isoformat(),
                )
                pending_confirmations += 1
                await push_service.notify_recurring_confirmation_pending(
                    db, rule.user_id, rule.name, rule.next_due_date
                )
                await db.commit()
                continue

            while rule.status == "active" and rule.next_due_date <= today:
                due_date = rule.next_due_date
                try:
                    await recurring_service.confirm_rule(
                        db,
                        rule.user_id,
                        rule.id,
                        RecurringConfirmRequest(id=uuid4(), date=due_date),
                    )
                    await db.commit()
                    generated += 1
                except Exception:
                    await db.rollback()
                    logger.error(
                        "generate_recurring_failed",
                        rule_id=str(rule.id),
                        due_date=due_date.isoformat(),
                    )
                    break

    logger.info(
        "generate_recurring_done",
        rules_checked=len(rules),
        generated=generated,
        pending_confirmations=pending_confirmations,
    )
