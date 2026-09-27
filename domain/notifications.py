"""Arma el `(title, body)` de cada tipo de aviso que el backend manda por
push (Expo Push API, ver `core/push.py`). Funciones puras: nada de montos
en el cuerpo (mismo criterio que "sin PII en logs" de CLAUDE.md, extendido
acá para no filtrar datos financieros en la pantalla de bloqueo del
teléfono) y texto en español apto para mostrar, igual que `message` en el
envelope de la API.
"""

from __future__ import annotations

from datetime import date


def budget_alert_message(
    category_name: str, percent_consumed: int, threshold: int
) -> tuple[str, str]:
    if threshold >= 100:
        return (
            "Presupuesto agotado",
            f"Ya usaste el {percent_consumed}% del presupuesto de {category_name} este mes.",
        )
    return (
        "Presupuesto casi al límite",
        f"Vas en {percent_consumed}% del presupuesto de {category_name} este mes.",
    )


def due_reminder_recurring_message(rule_name: str, due_date: date) -> tuple[str, str]:
    return ("Próximo pago", f"{rule_name} vence el {due_date.isoformat()}.")


def due_reminder_installment_message(
    plan_description: str, due_date: date, number: int
) -> tuple[str, str]:
    return (
        "Cuota próxima a vencer",
        f"La cuota {number} de {plan_description} vence el {due_date.isoformat()}.",
    )


def card_statement_message(account_name: str) -> tuple[str, str]:
    return ("Corte de tarjeta", f"Hoy es el corte de {account_name}.")


def card_payment_due_message(account_name: str) -> tuple[str, str]:
    return ("Pago de tarjeta", f"Hoy vence el pago de {account_name}.")


def recurring_confirmation_pending_message(rule_name: str, due_date: date) -> tuple[str, str]:
    return (
        "Confirma tu pago",
        f"{rule_name} tenía fecha el {due_date.isoformat()} — confírmalo cuando lo registres.",
    )


def spending_anomaly_message(category_name: str, percent_increase: int) -> tuple[str, str]:
    return (
        "Gasto fuera de lo normal",
        f"Gastaste {percent_increase}% más de lo usual en {category_name} el mes pasado.",
    )
