from datetime import date

from domain.notifications import (
    budget_alert_message,
    card_payment_due_message,
    card_statement_message,
    due_reminder_installment_message,
    due_reminder_recurring_message,
    recurring_confirmation_pending_message,
    spending_anomaly_message,
)


def test_budget_alert_message_100_says_agotado() -> None:
    title, body = budget_alert_message("Comida", 104, 100)
    assert "agotado" in title.lower()
    assert "Comida" in body
    assert "104" in body


def test_budget_alert_message_80_says_casi() -> None:
    title, body = budget_alert_message("Comida", 82, 80)
    assert "límite" in title.lower()
    assert "Comida" in body
    assert "82" in body


def test_due_reminder_recurring_message_includes_name_and_date() -> None:
    title, body = due_reminder_recurring_message("Netflix", date(2026, 9, 20))
    assert "Netflix" in body
    assert "2026-09-20" in body
    assert title


def test_due_reminder_installment_message_includes_number_and_date() -> None:
    title, body = due_reminder_installment_message("Laptop Dell", date(2026, 9, 20), 3)
    assert "Laptop Dell" in body
    assert "3" in body
    assert "2026-09-20" in body


def test_card_statement_message_includes_account_name() -> None:
    _, body = card_statement_message("BAC Visa")
    assert "BAC Visa" in body


def test_card_payment_due_message_includes_account_name() -> None:
    _, body = card_payment_due_message("BAC Visa")
    assert "BAC Visa" in body


def test_recurring_confirmation_pending_message_includes_name_and_date() -> None:
    _, body = recurring_confirmation_pending_message("Renta", date(2026, 9, 1))
    assert "Renta" in body
    assert "2026-09-01" in body


def test_spending_anomaly_message_includes_category_and_percent() -> None:
    _, body = spending_anomaly_message("Entretenimiento", 45)
    assert "Entretenimiento" in body
    assert "45" in body


def test_no_message_leaks_cents_amounts() -> None:
    """Ningún builder recibe ni imprime montos — solo nombres, porcentajes
    y fechas. Este test documenta la regla, no la puede verificar en
    runtime porque las firmas ya no aceptan montos."""
    import inspect

    import domain.notifications as notifications_module

    for name, fn in inspect.getmembers(notifications_module, inspect.isfunction):
        params = inspect.signature(fn).parameters
        assert "cents" not in " ".join(params), f"{name} no debería aceptar montos"
