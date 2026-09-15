import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from tests.api.helpers import create_account, create_category, register_and_login


async def _create_expense(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    account_id: uuid.UUID,
    category_id: uuid.UUID,
    amount_cents: int,
    date: str,
) -> uuid.UUID:
    transaction_id = uuid.uuid4()
    response = await client.post(
        "/v1/transactions",
        json={
            "id": str(transaction_id),
            "account_id": str(account_id),
            "category_id": str(category_id),
            "kind": "expense",
            "amount_cents": amount_cents,
            "date": date,
        },
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 200, response.text
    return transaction_id


def _current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


async def _create_budget(
    client: AsyncClient, headers: dict[str, str], category_id: uuid.UUID, amount_cents: int
) -> uuid.UUID:
    budget_id = uuid.uuid4()
    response = await client.post(
        "/v1/budgets",
        json={
            "id": str(budget_id),
            "name": "Presupuesto de prueba",
            "items": [
                {
                    "id": str(uuid.uuid4()),
                    "category_id": str(category_id),
                    "amount_cents": amount_cents,
                }
            ],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return budget_id


async def test_current_shows_progress_for_budgeted_category(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=100_000)
    category_id = await create_category(client, headers, name="Alimentación")
    await _create_budget(client, headers, category_id, 250_000)

    await _create_expense(
        client,
        headers,
        account_id=account_id,
        category_id=category_id,
        amount_cents=214_000,
        date=f"{_current_month()}-05",
    )

    response = await client.get("/v1/budgets/current", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["is_closed"] is False
    item = data["items"][0]
    assert item["budgeted_cents"] == 250_000
    assert item["spent_cents"] == 214_000
    assert item["available_cents"] == 36_000
    assert item["percent_consumed"] == 86


async def test_current_lists_unbudgeted_category_spend(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=100_000)
    budgeted_category = await create_category(client, headers, name="Alimentación")
    other_category = await create_category(client, headers, name="Mascotas")
    await _create_budget(client, headers, budgeted_category, 100_000)

    await _create_expense(
        client,
        headers,
        account_id=account_id,
        category_id=other_category,
        amount_cents=24_000,
        date=f"{_current_month()}-05",
    )

    response = await client.get("/v1/budgets/current", headers=headers)
    unbudgeted = response.json()["data"]["unbudgeted"]
    assert len(unbudgeted) == 1
    assert unbudgeted[0]["category_name"] == "Mascotas"
    assert unbudgeted[0]["spent_cents"] == 24_000


async def test_transfer_does_not_count_as_spend_in_budget(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    checking = await create_account(client, headers, initial_balance_cents=100_000)
    savings = await create_account(client, headers, initial_balance_cents=0)
    category_id = await create_category(client, headers, name="Alimentación")
    await _create_budget(client, headers, category_id, 100_000)

    await client.post(
        "/v1/transactions/transfer",
        json={
            "out_transaction_id": str(uuid.uuid4()),
            "in_transaction_id": str(uuid.uuid4()),
            "from_account_id": str(checking),
            "to_account_id": str(savings),
            "amount_cents": 50_000,
            "date": f"{_current_month()}-05",
        },
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    response = await client.get("/v1/budgets/current", headers=headers)
    data = response.json()["data"]
    assert data["total_spent_cents"] == 0
    assert data["unbudgeted"] == []


async def test_refund_subtracts_from_original_category_spend(client: AsyncClient) -> None:
    """Caso 5: un reembolso resta del gasto de su categoría, no suma a ingresos."""
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=500_000)
    category_id = await create_category(client, headers, name="Ropa")
    await _create_budget(client, headers, category_id, 200_000)

    expense_id = await _create_expense(
        client,
        headers,
        account_id=account_id,
        category_id=category_id,
        amount_cents=150_000,
        date=f"{_current_month()}-05",
    )

    refund = await client.post(
        f"/v1/transactions/{expense_id}/refund",
        json={
            "original_id": str(expense_id),
            "id": str(uuid.uuid4()),
            "amount_cents": 40_000,
            "date": f"{_current_month()}-08",
        },
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert refund.status_code == 200, refund.text

    response = await client.get("/v1/budgets/current", headers=headers)
    data = response.json()["data"]
    item = data["items"][0]
    assert item["spent_cents"] == 110_000
    assert item["available_cents"] == 90_000
    assert data["total_spent_cents"] == 110_000


async def test_no_active_budget_returns_404(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    response = await client.get("/v1/budgets/current", headers=headers)
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "NO_ACTIVE_BUDGET"


async def test_budget_item_requires_expense_category(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    income_category = await create_category(client, headers, kind="income", name="Salario")

    response = await client.post(
        "/v1/budgets",
        json={
            "id": str(uuid.uuid4()),
            "name": "Malo",
            "items": [
                {"id": str(uuid.uuid4()), "category_id": str(income_category), "amount_cents": 100}
            ],
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "BUDGET_REQUIRES_EXPENSE_CATEGORY"


async def test_cross_user_cannot_read_budget(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, "a@example.com")
    headers_b = await register_and_login(client, "b@example.com")
    category_id = await create_category(client, headers_a, name="Alimentación")
    budget_id = await _create_budget(client, headers_a, category_id, 100_000)

    response = await client.get(f"/v1/budgets/{budget_id}", headers=headers_b)
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "BUDGET_NOT_FOUND"


async def test_close_period_freezes_and_rejects_double_close(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    category_id = await create_category(client, headers, name="Alimentación")
    budget_id = await _create_budget(client, headers, category_id, 100_000)

    await _create_expense(
        client,
        headers,
        account_id=account_id,
        category_id=category_id,
        amount_cents=60_000,
        date=f"{_current_month()}-05",
    )

    close = await client.post(
        f"/v1/budgets/{budget_id}/close-period?month={_current_month()}", headers=headers
    )
    assert close.status_code == 200, close.text
    assert close.json()["data"]["items"][0]["spent_cents"] == 60_000

    again = await client.post(
        f"/v1/budgets/{budget_id}/close-period?month={_current_month()}", headers=headers
    )
    assert again.status_code == 409
    assert again.json()["data"]["code"] == "PERIOD_ALREADY_CLOSED"

    # /budgets/current para ese mes ahora devuelve lo congelado.
    current = await client.get(f"/v1/budgets/current?month={_current_month()}", headers=headers)
    assert current.json()["data"]["is_closed"] is True
    assert current.json()["data"]["items"][0]["spent_cents"] == 60_000

    history = await client.get(f"/v1/budgets/{budget_id}/history", headers=headers)
    assert len(history.json()["data"]["periods"]) == 1


# ---------------------------------------------------------------------------
# Gancho "tras cada escritura" (PLAN-backend.md §9): budget_service.check_alerts_for_category,
# llamado desde transaction_service al crear/editar/restaurar un gasto. Acá
# solo se verifica el log; el envío de push real (con su dedup) tiene sus
# propios tests en tests/api/test_push_notifications.py.


async def test_creating_expense_over_threshold_logs_budget_alert(
    client: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    category_id = await create_category(client, headers, name="Alimentación")
    await _create_budget(client, headers, category_id, 100_000)

    await _create_expense(
        client,
        headers,
        account_id=account_id,
        category_id=category_id,
        amount_cents=90_000,  # 90% del presupuesto
        date=f"{_current_month()}-05",
    )

    output = capsys.readouterr().out
    assert "budget_alert" in output
    assert str(category_id) in output


async def test_creating_expense_under_threshold_does_not_log_alert(
    client: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    category_id = await create_category(client, headers, name="Alimentación")
    await _create_budget(client, headers, category_id, 100_000)

    await _create_expense(
        client,
        headers,
        account_id=account_id,
        category_id=category_id,
        amount_cents=50_000,  # 50% del presupuesto
        date=f"{_current_month()}-05",
    )

    output = capsys.readouterr().out
    assert "budget_alert" not in output


async def test_updating_expense_category_across_threshold_logs_budget_alert(
    client: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    """`amount_cents` no es editable vía PATCH; `category_id` sí — mover un
    gasto grande hacia una categoría presupuestada es la forma real de
    cruzar el umbral con una edición."""
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    other_category = await create_category(client, headers, name="Otra")
    budgeted_category = await create_category(client, headers, name="Alimentación")
    await _create_budget(client, headers, budgeted_category, 100_000)

    transaction_id = await _create_expense(
        client,
        headers,
        account_id=account_id,
        category_id=other_category,
        amount_cents=95_000,
        date=f"{_current_month()}-05",
    )
    capsys.readouterr()  # descarta cualquier log de la creación (categoría sin presupuesto)

    response = await client.patch(
        f"/v1/transactions/{transaction_id}",
        json={"category_id": str(budgeted_category)},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    output = capsys.readouterr().out
    assert "budget_alert" in output
    assert str(budgeted_category) in output


async def test_expense_in_category_without_budget_does_not_log_alert(
    client: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    category_id = await create_category(client, headers, name="Sin presupuesto")

    await _create_expense(
        client,
        headers,
        account_id=account_id,
        category_id=category_id,
        amount_cents=500_000,
        date=f"{_current_month()}-05",
    )

    output = capsys.readouterr().out
    assert "budget_alert" not in output
