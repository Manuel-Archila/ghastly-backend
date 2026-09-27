"""Orquesta el envío de cada tipo de aviso: arma el mensaje con
`domain/notifications.py` y lo manda con `core/push.send_push_to_user`.

`notify_budget_alert` es el único que necesita dedup — CLAUDE.md documenta
que `check_alerts_for_category` se dispara en cada escritura mientras la
categoría siga sobre el umbral, y el job de las 20:00 corre aparte; sin
esto, push real mandaría un aviso por cada gasto nuevo. La reserva usa
`INSERT ... ON CONFLICT DO NOTHING` sobre `budget_alerts_sent` (un solo
round-trip, atómico entre el gancho y el job).

`notify_budget_alert` hace `flush()`, no `commit()`: se llama desde
`transaction_service._check_budget_alert` DENTRO de la transacción que
`core/idempotency.py::handle_idempotent_write` todavía no comprometió — un
commit acá adelantaría ese commit único y rompería la atomicidad
"todo o nada" documentada ahí. Quien llama a `notify_budget_alert` desde un
contexto que no vaya a comprometer solo (p. ej. `jobs/check_budget_alerts.py`)
es responsable de su propio `commit()`.

Todas las `notify_*` respetan `notification_preferences` (canal `push`
habilitado, fuera de horas de silencio) ANTES de mandar nada. En
`notify_budget_alert` ese chequeo va antes de reservar el cupo en
`budget_alerts_sent`: si se saltó por horas de silencio, el mismo umbral
puede volver a evaluarse más tarde en vez de darse por "ya avisado".
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core import push
from core.timezone import now_in_business_tz
from domain import notifications
from domain.quiet_hours import is_within_quiet_hours
from services import notification_preferences_service
from storage.models.notification import BudgetAlertSent, NotificationPreferences


def _can_send_now(prefs: NotificationPreferences) -> bool:
    if "push" not in prefs.channels:
        return False
    current = now_in_business_tz().time()
    return not is_within_quiet_hours(current, prefs.quiet_hours_start, prefs.quiet_hours_end)


async def _send_if_allowed(db: AsyncSession, user_id: UUID, title: str, body: str) -> None:
    prefs = await notification_preferences_service.get_or_create(db, user_id)
    if _can_send_now(prefs):
        await push.send_push_to_user(db, user_id, title, body)


async def notify_budget_alert(
    db: AsyncSession,
    user_id: UUID,
    category_id: UUID,
    category_name: str,
    month: str,
    percent_consumed: int,
    threshold: int,
) -> bool:
    prefs = await notification_preferences_service.get_or_create(db, user_id)
    if not _can_send_now(prefs):
        return False

    stmt = (
        insert(BudgetAlertSent)
        .values(user_id=user_id, category_id=category_id, month=month, threshold=threshold)
        .on_conflict_do_nothing(index_elements=["user_id", "category_id", "month", "threshold"])
        .returning(BudgetAlertSent.id)
    )
    result = await db.execute(stmt)
    reserved = result.first() is not None
    await db.flush()
    if not reserved:
        return False

    title, body = notifications.budget_alert_message(category_name, percent_consumed, threshold)
    await push.send_push_to_user(db, user_id, title, body)
    return True


async def notify_due_reminder_recurring(
    db: AsyncSession, user_id: UUID, rule_name: str, due_date: date
) -> None:
    title, body = notifications.due_reminder_recurring_message(rule_name, due_date)
    await _send_if_allowed(db, user_id, title, body)


async def notify_due_reminder_installment(
    db: AsyncSession, user_id: UUID, plan_description: str, due_date: date, number: int
) -> None:
    title, body = notifications.due_reminder_installment_message(plan_description, due_date, number)
    await _send_if_allowed(db, user_id, title, body)


async def notify_card_statement(db: AsyncSession, user_id: UUID, account_name: str) -> None:
    title, body = notifications.card_statement_message(account_name)
    await _send_if_allowed(db, user_id, title, body)


async def notify_card_payment_due(db: AsyncSession, user_id: UUID, account_name: str) -> None:
    title, body = notifications.card_payment_due_message(account_name)
    await _send_if_allowed(db, user_id, title, body)


async def notify_recurring_confirmation_pending(
    db: AsyncSession, user_id: UUID, rule_name: str, due_date: date
) -> None:
    title, body = notifications.recurring_confirmation_pending_message(rule_name, due_date)
    await _send_if_allowed(db, user_id, title, body)


async def notify_spending_anomaly(
    db: AsyncSession, user_id: UUID, category_name: str, percent_increase: int
) -> None:
    title, body = notifications.spending_anomaly_message(category_name, percent_increase)
    await _send_if_allowed(db, user_id, title, body)
