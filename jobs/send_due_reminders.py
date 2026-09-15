"""Job diario (08:00): aviso de vencimientos próximos (recurrentes + cuotas)."""

from __future__ import annotations

from datetime import timedelta

import structlog
from sqlalchemy import select

from core.timezone import today_in_business_tz
from services import push_service
from storage.db import get_session_factory
from storage.models.installment import Installment, InstallmentPlan
from storage.models.recurring import RecurringRule

logger = structlog.get_logger("jobs.send_due_reminders")

INSTALLMENT_REMINDER_DAYS = 3


async def run() -> None:
    today = today_in_business_tz()
    session_factory = get_session_factory()

    async with session_factory() as db:
        rules_result = await db.execute(
            select(RecurringRule).where(
                RecurringRule.deleted_at.is_(None),
                RecurringRule.status == "active",
                RecurringRule.next_due_date >= today,
            )
        )
        due_rules = [
            r
            for r in rules_result.scalars().all()
            if (r.next_due_date - today).days <= r.reminder_days_before
        ]

        installments_result = await db.execute(
            select(Installment, InstallmentPlan.description)
            .join(InstallmentPlan, InstallmentPlan.id == Installment.plan_id)
            .where(
                Installment.status == "pending",
                Installment.due_date >= today,
                Installment.due_date <= today + timedelta(days=INSTALLMENT_REMINDER_DAYS),
            )
        )
        due_installments = list(installments_result.all())

        for rule in due_rules:
            logger.info(
                "due_reminder_recurring",
                rule_id=str(rule.id),
                user_id=str(rule.user_id),
                due_date=rule.next_due_date.isoformat(),
            )
            await push_service.notify_due_reminder_recurring(
                db, rule.user_id, rule.name, rule.next_due_date
            )
        for installment, plan_description in due_installments:
            logger.info(
                "due_reminder_installment",
                installment_id=str(installment.id),
                user_id=str(installment.user_id),
                due_date=installment.due_date.isoformat(),
            )
            await push_service.notify_due_reminder_installment(
                db, installment.user_id, plan_description, installment.due_date, installment.number
            )
        await db.commit()

    logger.info(
        "send_due_reminders_done", recurring=len(due_rules), installments=len(due_installments)
    )
