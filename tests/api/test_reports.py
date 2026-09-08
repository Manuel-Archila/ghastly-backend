import uuid
from datetime import UTC, datetime

from httpx import AsyncClient

from tests.api.helpers import create_account, create_category, register_and_login


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def _current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


async def _create_transaction(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    account_id: uuid.UUID,
    kind: str,
    amount_cents: int,
    date: str,
    category_id: uuid.UUID | None = None,
) -> uuid.UUID:
    transaction_id = uuid.uuid4()
    payload: dict[str, object] = {
        "id": str(transaction_id),
        "account_id": str(account_id),
        "kind": kind,
        "amount_cents": amount_cents,
        "date": date,
    }
    if category_id is not None:
        payload["category_id"] = str(category_id)
    response = await client.post("/v1/transactions", json=payload, headers={**headers, **_idem()})
    assert response.status_code == 200, response.text
    return transaction_id


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


async def _create_debt(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    principal_cents: int,
    linked_account_id: uuid.UUID | None = None,
) -> uuid.UUID:
    debt_id = uuid.uuid4()
    payload: dict[str, object] = {
        "id": str(debt_id),
        "name": "Préstamo de prueba",
        "type": "personal_loan",
        "principal_cents": principal_cents,
        "monthly_interest_rate": "0.01",
        "start_date": "2026-01-01",
        "term_months": 12,
    }
    if linked_account_id is not None:
        payload["linked_account_id"] = str(linked_account_id)
    response = await client.post("/v1/debts", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return debt_id


async def _create_installment_plan(
    client: AsyncClient,
    headers: dict[str, str],
    account_id: uuid.UUID,
    *,
    description: str = "Laptop",
    total_amount_cents: int = 300_000,
    count: int = 3,
    first_payment_date: str = "2026-09-15",
) -> uuid.UUID:
    plan_id = uuid.uuid4()
    installment_ids = [uuid.uuid4() for _ in range(count)]
    response = await client.post(
        "/v1/installment-plans",
        json={
            "id": str(plan_id),
            "account_id": str(account_id),
            "description": description,
            "total_amount_cents": total_amount_cents,
            "installments_count": count,
            "first_payment_date": first_payment_date,
            "installment_ids": [str(i) for i in installment_ids],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return plan_id


async def _create_recurring_rule(
    client: AsyncClient,
    headers: dict[str, str],
    account_id: uuid.UUID,
    *,
    name: str = "Netflix",
    amount_cents: int = 8_900,
    next_due_date: str = "2026-09-20",
) -> uuid.UUID:
    rule_id = uuid.uuid4()
    response = await client.post(
        "/v1/recurring-rules",
        json={
            "id": str(rule_id),
            "account_id": str(account_id),
            "kind": "expense",
            "name": name,
            "amount_cents": amount_cents,
            "frequency": "monthly",
            "next_due_date": next_due_date,
            "auto_create": False,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return rule_id


async def test_dashboard_happy_path_aggregates_all_sections(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    checking_id = await create_account(
        client, headers, initial_balance_cents=500_000, name="Checking"
    )
    credit_card_id = await create_account(
        client, headers, account_type="credit_card", initial_balance_cents=0, name="Tarjeta"
    )
    comida_id = await create_category(client, headers, name="Comida")
    compras_id = await create_category(client, headers, name="Compras")
    month = _current_month()

    await _create_budget(client, headers, comida_id, 200_000)
    await _create_transaction(
        client,
        headers,
        account_id=checking_id,
        kind="expense",
        amount_cents=50_000,
        date=f"{month}-05",
        category_id=comida_id,
    )
    await _create_transaction(
        client,
        headers,
        account_id=credit_card_id,
        kind="expense",
        amount_cents=100_000,
        date=f"{month}-05",
        category_id=compras_id,
    )
    await _create_transaction(
        client,
        headers,
        account_id=checking_id,
        kind="income",
        amount_cents=300_000,
        date=f"{month}-03",
    )
    await _create_debt(client, headers, principal_cents=1_000_000)
    await _create_installment_plan(client, headers, checking_id)
    await _create_recurring_rule(client, headers, checking_id)

    response = await client.get("/v1/reports/dashboard", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["month"] == month
    # checking: 500_000 + 300_000 (ingreso) - 50_000 (gasto Comida) = 750_000
    # tarjeta: 0 + 100_000 (gasto, pasivo aumenta) = 100_000
    # deuda sin cuenta vinculada: 1_000_000
    assert data["net_worth"]["net_worth_cents"] == 750_000 - 100_000 - 1_000_000

    assert data["cashflow"]["income_cents"] == 300_000
    assert data["cashflow"]["expense_cents"] == 150_000
    assert data["cashflow"]["net_cents"] == 150_000

    top_names = [c["category_name"] for c in data["top_categories"]]
    assert top_names == ["Compras", "Comida"]

    assert data["budget"] is not None
    assert data["budget"]["total_budgeted_cents"] == 200_000

    assert data["installment_liability"]["total_pending_cents"] == 300_000

    upcoming_types = [item["source_type"] for item in data["upcoming"]]
    assert "installment" in upcoming_types
    assert "recurring" in upcoming_types
    due_dates = [item["due_date"] for item in data["upcoming"]]
    assert due_dates == sorted(due_dates)

    assert data["receivable_cents"] is None


async def test_dashboard_net_worth_treats_credit_card_balance_as_liability(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    await create_account(client, headers, initial_balance_cents=0, name="Checking")
    await create_account(
        client, headers, account_type="credit_card", initial_balance_cents=75_000, name="Tarjeta"
    )

    response = await client.get("/v1/reports/dashboard", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["net_worth"]["net_worth_cents"] == -75_000


async def test_dashboard_net_worth_treats_loan_balance_as_liability(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    await create_account(client, headers, initial_balance_cents=0, name="Checking")
    await create_account(
        client, headers, account_type="loan", initial_balance_cents=300_000, name="Préstamo"
    )

    response = await client.get("/v1/reports/dashboard", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["net_worth"]["net_worth_cents"] == -300_000


async def test_dashboard_defaults_to_current_month_when_month_omitted(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    await create_account(client, headers)

    response = await client.get("/v1/reports/dashboard", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["month"] == _current_month()


async def test_dashboard_top_categories_excludes_transfers(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_a = await create_account(client, headers, initial_balance_cents=500_000, name="A")
    account_b = await create_account(client, headers, initial_balance_cents=0, name="B")
    month = _current_month()

    transfer_id = uuid.uuid4()
    response = await client.post(
        "/v1/transactions/transfer",
        json={
            "id": str(transfer_id),
            "from_account_id": str(account_a),
            "to_account_id": str(account_b),
            "out_transaction_id": str(uuid.uuid4()),
            "in_transaction_id": str(uuid.uuid4()),
            "amount_cents": 100_000,
            "date": f"{month}-05",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text

    response = await client.get("/v1/reports/dashboard", headers=headers)
    data = response.json()["data"]
    assert data["top_categories"] == []
    assert data["cashflow"]["income_cents"] == 0
    assert data["cashflow"]["expense_cents"] == 0


async def test_dashboard_top_categories_limited_to_five(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    month = _current_month()

    for i in range(6):
        category_id = await create_category(client, headers, name=f"Categoria {i}")
        await _create_transaction(
            client,
            headers,
            account_id=account_id,
            kind="expense",
            amount_cents=(i + 1) * 1_000,
            date=f"{month}-05",
            category_id=category_id,
        )

    response = await client.get("/v1/reports/dashboard", headers=headers)
    data = response.json()["data"]
    assert len(data["top_categories"]) == 5
    amounts = [c["net_spent_cents"] for c in data["top_categories"]]
    assert amounts == sorted(amounts, reverse=True)


async def test_dashboard_receivable_cents_is_always_null(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    await create_account(client, headers)

    response = await client.get("/v1/reports/dashboard", headers=headers)
    assert response.json()["data"]["receivable_cents"] is None


async def test_dashboard_empty_state_returns_zeros_not_errors(client: AsyncClient) -> None:
    headers = await register_and_login(client)

    response = await client.get("/v1/reports/dashboard", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["net_worth"]["net_worth_cents"] == 0
    assert data["cashflow"] == {"income_cents": 0, "expense_cents": 0, "net_cents": 0}
    assert data["top_categories"] == []
    assert data["budget"] is None
    assert data["upcoming"] == []
    assert data["installment_liability"]["total_pending_cents"] == 0
    assert data["receivable_cents"] is None


async def test_dashboard_isolated_from_other_users_data(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, email="user-a@example.com")
    headers_b = await register_and_login(client, email="user-b@example.com")

    await create_account(client, headers_a, initial_balance_cents=1_000_000, name="Cuenta A")
    await create_account(client, headers_b, initial_balance_cents=50_000, name="Cuenta B")

    response_b = await client.get("/v1/reports/dashboard", headers=headers_b)
    assert response_b.status_code == 200, response_b.text
    assert response_b.json()["data"]["net_worth"]["net_worth_cents"] == 50_000
