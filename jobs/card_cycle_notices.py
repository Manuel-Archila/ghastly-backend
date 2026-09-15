"""Job diario: aviso de corte y fecha de pago de tarjetas de crédito.

Usa `domain/credit_cycle.py`."""

from __future__ import annotations

import structlog
from sqlalchemy import select

from core.timezone import today_in_business_tz
from domain.credit_cycle import compute_current_cycle
from services import push_service
from storage.db import get_session_factory
from storage.models.account import Account

logger = structlog.get_logger("jobs.card_cycle_notices")

NOTICE_DAYS_BEFORE = (0, 3)  # avisa el mismo día y 3 días antes


async def run() -> None:
    today = today_in_business_tz()
    session_factory = get_session_factory()
    notices = 0

    async with session_factory() as db:
        result = await db.execute(
            select(Account).where(
                Account.type == "credit_card",
                Account.deleted_at.is_(None),
                Account.is_archived.is_(False),
                Account.statement_day.is_not(None),
                Account.payment_due_day.is_not(None),
            )
        )
        accounts = list(result.scalars().all())

        for account in accounts:
            cycle = compute_current_cycle(today, account.statement_day, account.payment_due_day)  # type: ignore[arg-type]
            if cycle.days_until_statement in NOTICE_DAYS_BEFORE:
                logger.info(
                    "card_statement_notice",
                    account_id=str(account.id),
                    user_id=str(account.user_id),
                    days_until_statement=cycle.days_until_statement,
                )
                notices += 1
                await push_service.notify_card_statement(db, account.user_id, account.name)
            if cycle.days_until_payment_due in NOTICE_DAYS_BEFORE:
                logger.info(
                    "card_payment_due_notice",
                    account_id=str(account.id),
                    user_id=str(account.user_id),
                    days_until_payment_due=cycle.days_until_payment_due,
                )
                notices += 1
                await push_service.notify_card_payment_due(db, account.user_id, account.name)
        await db.commit()

    logger.info("card_cycle_notices_done", accounts=len(accounts), notices=notices)
