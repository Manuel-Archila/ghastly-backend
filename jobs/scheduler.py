"""Jobs programados con APScheduler, en hora de Guatemala (CLAUDE.md).

Cada job es idempotente y re-ejecutable por diseño — ver el docstring de
cada uno para el porqué específico."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from core.timezone import BUSINESS_TIMEZONE
from jobs import (
    card_cycle_notices,
    check_budget_alerts,
    close_budget_periods,
    detect_anomalies,
    generate_recurring,
    purge_idempotency_keys,
    refresh_fx_rates,
    send_due_reminders,
)

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        scheduler = AsyncIOScheduler(timezone=BUSINESS_TIMEZONE)
        scheduler.add_job(
            generate_recurring.run, CronTrigger(hour=0, minute=5), id="generate_recurring"
        )
        scheduler.add_job(
            send_due_reminders.run, CronTrigger(hour=8, minute=0), id="send_due_reminders"
        )
        scheduler.add_job(
            check_budget_alerts.run, CronTrigger(hour=20, minute=0), id="check_budget_alerts"
        )
        scheduler.add_job(
            close_budget_periods.run,
            CronTrigger(day=1, hour=0, minute=15),
            id="close_budget_periods",
        )
        scheduler.add_job(
            card_cycle_notices.run, CronTrigger(hour=6, minute=0), id="card_cycle_notices"
        )
        scheduler.add_job(
            detect_anomalies.run,
            CronTrigger(day=1, hour=6, minute=0),
            id="detect_anomalies",
        )
        scheduler.add_job(
            purge_idempotency_keys.run, CronTrigger(minute=0), id="purge_idempotency_keys"
        )
        scheduler.add_job(
            refresh_fx_rates.run, CronTrigger(hour=7, minute=0), id="refresh_fx_rates"
        )
        _scheduler = scheduler
    return _scheduler


def start_scheduler() -> None:
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
