"""Caso de negocio 4: el monto en moneda extranjera se congela (`fx_rate`,
`base_amount_cents`) y presupuesto/reportes deben sumar en GTQ, no el monto
crudo. Esta suite existe porque, hasta este arreglo, ningún cálculo de
presupuesto ni de reportes usaba `base_amount_cents` — todos sumaban
`amount_cents` a secas (un gasto de $10 contaba como Q10, no como el
equivalente real).
"""

import uuid
from datetime import date, timedelta

from httpx import AsyncClient

from tests.api.helpers import (
    create_account,
    create_category,
    default_expense_category,
    register_and_login,
)

FX_RATE = "7.75"  # 1 USD = Q7.75, tasa de prueba


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def _create_budget(
    client: AsyncClient, headers: dict[str, str], category_id: uuid.UUID
) -> None:
    response = await client.post(
        "/v1/budgets",
        json={
            "id": str(uuid.uuid4()),
            "name": "Presupuesto de prueba",
            "items": [
                {"id": str(uuid.uuid4()), "category_id": str(category_id), "amount_cents": 100_000}
            ],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text


async def _create_usd_expense(
    client: AsyncClient,
    headers: dict[str, str],
    account_id: uuid.UUID,
    category_id: uuid.UUID,
    *,
    amount_cents: int = 1_000,  # $10.00
    date: str = "2026-09-05",
) -> uuid.UUID:
    txn_id = uuid.uuid4()
    response = await client.post(
        "/v1/transactions",
        json={
            "id": str(txn_id),
            "account_id": str(account_id),
            "category_id": str(category_id),
            "kind": "expense",
            "amount_cents": amount_cents,
            "currency": "USD",
            "fx_rate": FX_RATE,
            "date": date,
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text
    return txn_id


async def test_usd_expense_counts_converted_in_budget(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(
        client, headers, currency="USD", initial_balance_cents=100_000
    )
    category_id = await create_category(client, headers, name="Suscripciones")
    await _create_budget(client, headers, category_id)

    await _create_usd_expense(client, headers, account_id, category_id)

    current = await client.get("/v1/budgets/current", headers=headers)
    item = current.json()["data"]["items"][0]
    assert item["spent_cents"] == 7_750  # $10.00 * 7.75, no 1_000


async def test_usd_expense_counts_converted_in_dashboard_cashflow(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(
        client, headers, currency="USD", initial_balance_cents=100_000
    )
    category_id = await create_category(client, headers, name="Suscripciones")

    await _create_usd_expense(client, headers, account_id, category_id, date="2026-09-05")

    dashboard = await client.get("/v1/reports/dashboard?month=2026-09", headers=headers)
    assert dashboard.json()["data"]["cashflow"]["expense_cents"] == 7_750


async def test_stats_by_account_stays_native_currency(client: AsyncClient) -> None:
    """GET /transactions/stats?account_id= de una cuenta en USD debe mostrar
    el total en USD (nativo) — no tiene sentido convertirlo a GTQ cuando ya
    filtraste a una sola cuenta y esperás ver SU moneda."""
    headers = await register_and_login(client)
    account_id = await create_account(
        client, headers, currency="USD", initial_balance_cents=100_000
    )
    category_id = await create_category(client, headers, name="Suscripciones")

    await _create_usd_expense(client, headers, account_id, category_id)

    stats = await client.get(f"/v1/transactions/stats?account_id={account_id}", headers=headers)
    assert stats.json()["data"]["total_expense_cents"] == 1_000  # USD nativo, no convertido


async def test_comparison_rollup_converts_to_gtq(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(
        client, headers, currency="USD", initial_balance_cents=100_000
    )
    category_id = await create_category(client, headers, name="Suscripciones")

    await _create_usd_expense(client, headers, account_id, category_id, date="2026-09-05")

    comparison = await client.get("/v1/reports/comparison?a=2026-08&b=2026-09", headers=headers)
    assert comparison.json()["data"]["b_expense_cents"] == 7_750


async def test_recurring_rule_in_usd_requires_fx_rate(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(
        client, headers, currency="USD", initial_balance_cents=100_000
    )

    response = await client.post(
        "/v1/recurring-rules",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "name": "Netflix",
            "amount_cents": 999,
            "currency": "USD",
            "frequency": "monthly",
            "next_due_date": "2026-09-10",
            "auto_create": False,
        },
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert response.json()["data"]["code"] == "FX_RATE_REQUIRED"


async def test_confirming_usd_subscription_freezes_rate_and_counts_in_budget(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(
        client, headers, currency="USD", initial_balance_cents=100_000
    )
    category_id = await create_category(client, headers, name="Suscripciones")
    await _create_budget(client, headers, category_id)

    rule_id = uuid.uuid4()
    response = await client.post(
        "/v1/recurring-rules",
        json={
            "id": str(rule_id),
            "account_id": str(account_id),
            "category_id": str(category_id),
            "kind": "expense",
            "name": "Netflix",
            "amount_cents": 999,  # $9.99
            "currency": "USD",
            "fx_rate": FX_RATE,
            "frequency": "monthly",
            "next_due_date": "2026-09-10",
            "auto_create": False,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text

    confirm = await client.post(
        f"/v1/recurring-rules/{rule_id}/confirm",
        json={"id": str(uuid.uuid4()), "date": "2026-09-10"},
        headers={**headers, **_idem()},
    )
    assert confirm.status_code == 200, confirm.text

    current = await client.get("/v1/budgets/current", headers=headers)
    item = current.json()["data"]["items"][0]
    # 999 * 7.75 = 7742.25 -> redondeo a 7742 (ROUND_HALF_UP en centavos)
    assert item["spent_cents"] == 7_742


async def test_refund_of_usd_expense_nets_converted_amount(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(
        client, headers, currency="USD", initial_balance_cents=100_000
    )
    category_id = await create_category(client, headers, name="Suscripciones")
    await _create_budget(client, headers, category_id)

    txn_id = await _create_usd_expense(client, headers, account_id, category_id)

    refund = await client.post(
        f"/v1/transactions/{txn_id}/refund",
        json={"id": str(uuid.uuid4()), "fx_rate": FX_RATE},
        headers={**headers, **_idem()},
    )
    assert refund.status_code == 200, refund.text

    current = await client.get("/v1/budgets/current", headers=headers)
    item = current.json()["data"]["items"][0]
    assert item["spent_cents"] == 0  # el reembolso neteó el gasto en GTQ, no en crudo


async def test_upcoming_exposes_currency_for_usd_subscription(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(
        client, headers, currency="USD", initial_balance_cents=100_000
    )

    await client.post(
        "/v1/recurring-rules",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "name": "Netflix",
            "amount_cents": 999,
            "currency": "USD",
            "fx_rate": FX_RATE,
            "frequency": "monthly",
            "next_due_date": (date.today() + timedelta(days=10)).isoformat(),
            "auto_create": False,
            "reminder_days_before": 30,
        },
        headers=headers,
    )

    upcoming = await client.get("/v1/reports/upcoming?days=30", headers=headers)
    items = upcoming.json()["data"]["items"]
    netflix = next(i for i in items if i["name"] == "Netflix")
    assert netflix["currency"] == "USD"
    assert netflix["amount_cents"] == 999  # el calendario muestra el monto nativo, no convertido
