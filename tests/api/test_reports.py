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
    is_extraordinary: bool = False,
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
    if is_extraordinary:
        payload["is_extraordinary"] = True
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
    start_date: str = "2026-01-01",
    monthly_payment_cents: int | None = None,
) -> uuid.UUID:
    debt_id = uuid.uuid4()
    payload: dict[str, object] = {
        "id": str(debt_id),
        "name": "Préstamo de prueba",
        "type": "personal_loan",
        "principal_cents": principal_cents,
        "monthly_interest_rate": "0.01",
        "start_date": start_date,
        "term_months": 12,
    }
    if linked_account_id is not None:
        payload["linked_account_id"] = str(linked_account_id)
    if monthly_payment_cents is not None:
        payload["monthly_payment_cents"] = monthly_payment_cents
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


async def test_by_category_computes_percent_of_total_for_expense(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    comida_id = await create_category(client, headers, name="Comida")
    transporte_id = await create_category(client, headers, name="Transporte")
    month = _current_month()

    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="expense",
        amount_cents=300_00,
        date=f"{month}-05",
        category_id=comida_id,
    )
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="expense",
        amount_cents=100_00,
        date=f"{month}-06",
        category_id=transporte_id,
    )

    response = await client.get(
        f"/v1/reports/by-category?from={month}-01&to={month}-28&kind=expense", headers=headers
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["kind"] == "expense"
    assert data["total_cents"] == 400_00
    assert [item["category_name"] for item in data["items"]] == ["Comida", "Transporte"]
    assert [item["percent_of_total"] for item in data["items"]] == [75, 25]


async def test_by_category_kind_income_excludes_refunds(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    ropa_id = await create_category(client, headers, name="Ropa")
    salario_id = await create_category(client, headers, name="Salario", kind="income")
    month = _current_month()

    expense_id = await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="expense",
        amount_cents=100_000,
        date=f"{month}-05",
        category_id=ropa_id,
    )
    refund_response = await client.post(
        f"/v1/transactions/{expense_id}/refund",
        json={"id": str(uuid.uuid4()), "amount_cents": 30_000, "date": f"{month}-08"},
        headers={**headers, **_idem()},
    )
    assert refund_response.status_code == 200, refund_response.text
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="income",
        amount_cents=200_000,
        date=f"{month}-10",
        category_id=salario_id,
    )

    income_response = await client.get(
        f"/v1/reports/by-category?from={month}-01&to={month}-28&kind=income", headers=headers
    )
    income_data = income_response.json()["data"]
    assert [item["category_name"] for item in income_data["items"]] == ["Salario"]
    assert income_data["total_cents"] == 200_000

    expense_response = await client.get(
        f"/v1/reports/by-category?from={month}-01&to={month}-28&kind=expense", headers=headers
    )
    expense_data = expense_response.json()["data"]
    assert expense_data["items"][0]["category_name"] == "Ropa"
    assert expense_data["items"][0]["amount_cents"] == 70_000


async def test_by_category_defaults_to_current_month_when_dates_omitted(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    await create_account(client, headers)

    response = await client.get("/v1/reports/by-category", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["kind"] == "expense"
    assert data["items"] == []


async def test_by_category_isolated_from_other_users_data(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, email="cat-a@example.com")
    headers_b = await register_and_login(client, email="cat-b@example.com")
    account_a = await create_account(client, headers_a)
    category_a = await create_category(client, headers_a, name="Solo de A")
    month = _current_month()

    await _create_transaction(
        client,
        headers_a,
        account_id=account_a,
        kind="expense",
        amount_cents=50_000,
        date=f"{month}-05",
        category_id=category_a,
    )

    response_b = await client.get(
        f"/v1/reports/by-category?from={month}-01&to={month}-28&kind=expense", headers=headers_b
    )
    assert response_b.json()["data"]["items"] == []


async def test_cashflow_month_granularity_buckets_by_month(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)

    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="income",
        amount_cents=300_000,
        date="2026-07-10",
    )
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="expense",
        amount_cents=100_000,
        date="2026-07-15",
    )
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="income",
        amount_cents=200_000,
        date="2026-08-05",
    )

    response = await client.get(
        "/v1/reports/cashflow?from=2026-07-01&to=2026-08-31&granularity=month", headers=headers
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["granularity"] == "month"
    periods = data["periods"]
    assert len(periods) == 2
    assert periods[0]["period_start"] == "2026-07-01"
    assert periods[0]["income_cents"] == 300_000
    assert periods[0]["expense_cents"] == 100_000
    assert periods[0]["net_cents"] == 200_000
    assert periods[1]["period_start"] == "2026-08-01"
    assert periods[1]["income_cents"] == 200_000
    assert periods[1]["expense_cents"] == 0


async def test_cashflow_fills_gaps_with_zero_periods(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="income",
        amount_cents=100_000,
        date="2026-07-10",
    )

    response = await client.get(
        "/v1/reports/cashflow?from=2026-07-01&to=2026-09-30&granularity=month", headers=headers
    )
    periods = response.json()["data"]["periods"]
    assert [p["period_start"] for p in periods] == ["2026-07-01", "2026-08-01", "2026-09-01"]
    assert periods[1]["income_cents"] == 0
    assert periods[1]["expense_cents"] == 0


async def test_cashflow_week_granularity_aligns_to_monday(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    # 2026-09-08 es martes -> la semana que lo contiene arranca el lunes 2026-09-07.
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="income",
        amount_cents=50_000,
        date="2026-09-08",
    )

    response = await client.get(
        "/v1/reports/cashflow?from=2026-09-07&to=2026-09-13&granularity=week", headers=headers
    )
    data = response.json()["data"]
    assert data["periods"] == [
        {
            "period_start": "2026-09-07",
            "income_cents": 50_000,
            "expense_cents": 0,
            "net_cents": 50_000,
        }
    ]


async def test_cashflow_excludes_transfers(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_a = await create_account(client, headers, initial_balance_cents=500_000, name="A")
    account_b = await create_account(client, headers, initial_balance_cents=0, name="B")

    response = await client.post(
        "/v1/transactions/transfer",
        json={
            "id": str(uuid.uuid4()),
            "from_account_id": str(account_a),
            "to_account_id": str(account_b),
            "out_transaction_id": str(uuid.uuid4()),
            "in_transaction_id": str(uuid.uuid4()),
            "amount_cents": 100_000,
            "date": "2026-07-15",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text

    response = await client.get(
        "/v1/reports/cashflow?from=2026-07-01&to=2026-07-31&granularity=month", headers=headers
    )
    periods = response.json()["data"]["periods"]
    assert periods[0]["income_cents"] == 0
    assert periods[0]["expense_cents"] == 0


async def test_cashflow_defaults_to_current_month_when_dates_omitted(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    await create_account(client, headers)

    response = await client.get("/v1/reports/cashflow", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["granularity"] == "month"
    assert len(data["periods"]) == 1


async def test_cashflow_isolated_from_other_users_data(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, email="flow-a@example.com")
    headers_b = await register_and_login(client, email="flow-b@example.com")
    account_a = await create_account(client, headers_a)

    await _create_transaction(
        client,
        headers_a,
        account_id=account_a,
        kind="income",
        amount_cents=500_000,
        date="2026-07-10",
    )

    response_b = await client.get(
        "/v1/reports/cashflow?from=2026-07-01&to=2026-07-31&granularity=month", headers=headers_b
    )
    periods = response_b.json()["data"]["periods"]
    assert periods[0]["income_cents"] == 0


async def test_expected_income_computes_previous_month_and_avg_3m(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)

    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="income",
        amount_cents=100_000,
        date="2026-06-10",
    )
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="income",
        amount_cents=200_000,
        date="2026-07-10",
    )
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="income",
        amount_cents=300_000,
        date="2026-08-10",
    )

    response = await client.get("/v1/reports/expected-income?month=2026-09", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["month"] == "2026-09"
    assert data["previous_month_cents"] == 300_000
    assert data["avg_3m_cents"] == 200_000


async def test_expected_income_excludes_transfers(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_a = await create_account(client, headers, initial_balance_cents=500_000, name="A")
    account_b = await create_account(client, headers, initial_balance_cents=0, name="B")

    response = await client.post(
        "/v1/transactions/transfer",
        json={
            "id": str(uuid.uuid4()),
            "from_account_id": str(account_a),
            "to_account_id": str(account_b),
            "out_transaction_id": str(uuid.uuid4()),
            "in_transaction_id": str(uuid.uuid4()),
            "amount_cents": 100_000,
            "date": "2026-08-10",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text

    response = await client.get("/v1/reports/expected-income?month=2026-09", headers=headers)
    assert response.json()["data"]["previous_month_cents"] == 0


async def test_expected_income_defaults_to_current_month_when_omitted(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    await create_account(client, headers)

    response = await client.get("/v1/reports/expected-income", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["month"] == _current_month()


async def test_expected_income_isolated_from_other_users_data(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, email="income-a@example.com")
    headers_b = await register_and_login(client, email="income-b@example.com")
    account_a = await create_account(client, headers_a)

    await _create_transaction(
        client,
        headers_a,
        account_id=account_a,
        kind="income",
        amount_cents=500_000,
        date="2026-08-10",
    )

    response_b = await client.get("/v1/reports/expected-income?month=2026-09", headers=headers_b)
    assert response_b.json()["data"]["previous_month_cents"] == 0


# ---------------------------------------------------------------------------
# /reports/net-worth


async def test_net_worth_history_last_point_matches_dashboard_net_worth(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    await create_account(client, headers, initial_balance_cents=500_000)

    dashboard = await client.get("/v1/reports/dashboard", headers=headers)
    expected = dashboard.json()["data"]["net_worth"]["net_worth_cents"]

    response = await client.get("/v1/reports/net-worth?months=3", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["months"] == 3
    assert len(data["points"]) == 3
    assert data["points"][-1]["month"] == _current_month()
    assert data["points"][-1]["amount_cents"] == expected


async def test_net_worth_history_treats_credit_card_as_liability(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    await create_account(client, headers, initial_balance_cents=0)
    await create_account(
        client, headers, account_type="credit_card", initial_balance_cents=75_000, name="Tarjeta"
    )

    response = await client.get("/v1/reports/net-worth?months=1", headers=headers)
    assert response.json()["data"]["points"][-1]["amount_cents"] == -75_000


async def test_net_worth_history_subtracts_unlinked_debt(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    await create_account(client, headers, initial_balance_cents=1_000_000)
    await _create_debt(client, headers, principal_cents=200_000)

    response = await client.get("/v1/reports/net-worth?months=1", headers=headers)
    assert response.json()["data"]["points"][-1]["amount_cents"] == 800_000


async def test_net_worth_history_earlier_points_exclude_later_transactions(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=0)
    month = _current_month()
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="income",
        amount_cents=100_000,
        date=f"{month}-05",
    )

    response = await client.get("/v1/reports/net-worth?months=3", headers=headers)
    points = response.json()["data"]["points"]
    assert points[0]["amount_cents"] == 0  # 2 meses atrás, esta transacción no había ocurrido
    assert points[-1]["amount_cents"] == 100_000


async def test_net_worth_history_defaults_to_12_months(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    await create_account(client, headers)

    response = await client.get("/v1/reports/net-worth", headers=headers)
    data = response.json()["data"]
    assert data["months"] == 12
    assert len(data["points"]) == 12


async def test_net_worth_history_isolated_from_other_users_data(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, email="networth-a@example.com")
    headers_b = await register_and_login(client, email="networth-b@example.com")
    await create_account(client, headers_a, initial_balance_cents=1_000_000, name="Cuenta A")
    await create_account(client, headers_b, initial_balance_cents=50_000, name="Cuenta B")

    response_b = await client.get("/v1/reports/net-worth?months=1", headers=headers_b)
    assert response_b.json()["data"]["points"][-1]["amount_cents"] == 50_000


# ---------------------------------------------------------------------------
# /reports/trends


async def test_trends_computes_monthly_periods_and_income_average(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    month = _current_month()
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="income",
        amount_cents=300_000,
        date=f"{month}-05",
    )
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="expense",
        amount_cents=100_000,
        date=f"{month}-06",
    )

    response = await client.get("/v1/reports/trends?months=1", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["months"] == 1
    assert len(data["periods"]) == 1
    assert data["periods"][0]["income_cents"] == 300_000
    assert data["periods"][0]["expense_cents"] == 100_000
    assert data["periods"][0]["net_cents"] == 200_000
    assert data["avg_income_with_extraordinary_cents"] == 300_000
    assert data["avg_income_recurring_cents"] == 300_000


async def test_trends_splits_extraordinary_income_average(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=0)
    month = _current_month()
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="income",
        amount_cents=100_000,
        date=f"{month}-05",
    )
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="income",
        amount_cents=500_000,
        date=f"{month}-06",
        is_extraordinary=True,
    )

    response = await client.get("/v1/reports/trends?months=1", headers=headers)
    data = response.json()["data"]
    assert data["avg_income_with_extraordinary_cents"] == 600_000
    assert data["avg_income_recurring_cents"] == 100_000


async def test_trends_income_average_excludes_refunds(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    ropa_id = await create_category(client, headers, name="Ropa")
    month = _current_month()
    expense_id = await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="expense",
        amount_cents=100_000,
        date=f"{month}-05",
        category_id=ropa_id,
    )
    await client.post(
        f"/v1/transactions/{expense_id}/refund",
        json={"id": str(uuid.uuid4()), "amount_cents": 30_000, "date": f"{month}-08"},
        headers={**headers, **_idem()},
    )

    response = await client.get("/v1/reports/trends?months=1", headers=headers)
    data = response.json()["data"]
    # el flujo de caja bruto sí ve el reembolso como ingreso...
    assert data["periods"][0]["income_cents"] == 30_000
    # ...pero el promedio de ingreso real (caso de negocio 5) no lo cuenta.
    assert data["avg_income_with_extraordinary_cents"] == 0
    assert data["avg_income_recurring_cents"] == 0


async def test_trends_defaults_to_six_months(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    await create_account(client, headers)

    response = await client.get("/v1/reports/trends", headers=headers)
    data = response.json()["data"]
    assert data["months"] == 6
    assert len(data["periods"]) == 6


async def test_trends_isolated_from_other_users_data(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, email="trends-a@example.com")
    headers_b = await register_and_login(client, email="trends-b@example.com")
    account_a = await create_account(client, headers_a, initial_balance_cents=1_000_000)
    month = _current_month()
    await _create_transaction(
        client,
        headers_a,
        account_id=account_a,
        kind="income",
        amount_cents=500_000,
        date=f"{month}-05",
    )

    response_b = await client.get("/v1/reports/trends?months=1", headers=headers_b)
    data = response_b.json()["data"]
    assert data["periods"][0]["income_cents"] == 0
    assert data["avg_income_with_extraordinary_cents"] == 0


# ---------------------------------------------------------------------------
# /reports/comparison


async def test_comparison_computes_totals_and_percent_change(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="income",
        amount_cents=300_000,
        date="2026-08-05",
    )
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="income",
        amount_cents=330_000,
        date="2026-09-05",
    )
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="expense",
        amount_cents=100_000,
        date="2026-08-06",
    )
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="expense",
        amount_cents=150_000,
        date="2026-09-06",
    )

    response = await client.get("/v1/reports/comparison?a=2026-08&b=2026-09", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["a_month"] == "2026-08"
    assert data["b_month"] == "2026-09"
    assert data["a_income_cents"] == 300_000
    assert data["b_income_cents"] == 330_000
    assert data["income_change_percent"] == 10
    assert data["a_expense_cents"] == 100_000
    assert data["b_expense_cents"] == 150_000
    assert data["expense_change_percent"] == 50


async def test_comparison_category_breakdown_includes_both_months(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    comida_id = await create_category(client, headers, name="Comida")
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="expense",
        amount_cents=100_00,
        date="2026-08-05",
        category_id=comida_id,
    )
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="expense",
        amount_cents=150_00,
        date="2026-09-05",
        category_id=comida_id,
    )

    response = await client.get("/v1/reports/comparison?a=2026-08&b=2026-09", headers=headers)
    categories = response.json()["data"]["categories"]
    assert len(categories) == 1
    assert categories[0]["category_name"] == "Comida"
    assert categories[0]["a_amount_cents"] == 100_00
    assert categories[0]["b_amount_cents"] == 150_00
    assert categories[0]["delta_cents"] == 50_00


async def test_comparison_excludes_transfers(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_a = await create_account(client, headers, initial_balance_cents=500_000, name="A")
    account_b = await create_account(client, headers, initial_balance_cents=0, name="B")

    response = await client.post(
        "/v1/transactions/transfer",
        json={
            "id": str(uuid.uuid4()),
            "from_account_id": str(account_a),
            "to_account_id": str(account_b),
            "out_transaction_id": str(uuid.uuid4()),
            "in_transaction_id": str(uuid.uuid4()),
            "amount_cents": 100_000,
            "date": "2026-09-05",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text

    response = await client.get("/v1/reports/comparison?a=2026-08&b=2026-09", headers=headers)
    data = response.json()["data"]
    assert data["b_income_cents"] == 0
    assert data["b_expense_cents"] == 0


async def test_comparison_isolated_from_other_users_data(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, email="comparison-a@example.com")
    headers_b = await register_and_login(client, email="comparison-b@example.com")
    account_a = await create_account(client, headers_a, initial_balance_cents=500_000)
    await _create_transaction(
        client,
        headers_a,
        account_id=account_a,
        kind="income",
        amount_cents=500_000,
        date="2026-08-05",
    )

    response_b = await client.get("/v1/reports/comparison?a=2026-08&b=2026-09", headers=headers_b)
    data = response_b.json()["data"]
    assert data["a_income_cents"] == 0
    assert data["categories"] == []


# ---------------------------------------------------------------------------
# /reports/anomalies


async def test_anomalies_detects_category_spending_above_average(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    comida_id = await create_category(client, headers, name="Comida")
    for month in ("2026-05", "2026-06", "2026-07"):
        await _create_transaction(
            client,
            headers,
            account_id=account_id,
            kind="expense",
            amount_cents=500_00,
            date=f"{month}-05",
            category_id=comida_id,
        )
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="expense",
        amount_cents=700_00,
        date="2026-08-05",
        category_id=comida_id,
    )

    response = await client.get("/v1/reports/anomalies?month=2026-08", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["month"] == "2026-08"
    assert len(data["items"]) == 1
    assert data["items"][0]["category_name"] == "Comida"
    assert data["items"][0]["current_cents"] == 700_00
    assert data["items"][0]["average_cents"] == 500_00
    assert data["items"][0]["percent_increase"] == 40


async def test_anomalies_ignores_increase_below_threshold(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    comida_id = await create_category(client, headers, name="Comida")
    for month in ("2026-05", "2026-06", "2026-07"):
        await _create_transaction(
            client,
            headers,
            account_id=account_id,
            kind="expense",
            amount_cents=500_00,
            date=f"{month}-05",
            category_id=comida_id,
        )
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="expense",
        amount_cents=550_00,
        date="2026-08-05",
        category_id=comida_id,
    )

    response = await client.get("/v1/reports/anomalies?month=2026-08", headers=headers)
    assert response.json()["data"]["items"] == []


async def test_anomalies_defaults_to_current_month(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    await create_account(client, headers)

    response = await client.get("/v1/reports/anomalies", headers=headers)
    assert response.json()["data"]["month"] == _current_month()


async def test_anomalies_isolated_from_other_users_data(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, email="anomalies-a@example.com")
    headers_b = await register_and_login(client, email="anomalies-b@example.com")
    account_a = await create_account(client, headers_a, initial_balance_cents=1_000_000)
    comida_id = await create_category(client, headers_a, name="Comida")
    for month in ("2026-05", "2026-06", "2026-07"):
        await _create_transaction(
            client,
            headers_a,
            account_id=account_a,
            kind="expense",
            amount_cents=500_00,
            date=f"{month}-05",
            category_id=comida_id,
        )
    await _create_transaction(
        client,
        headers_a,
        account_id=account_a,
        kind="expense",
        amount_cents=700_00,
        date="2026-08-05",
        category_id=comida_id,
    )

    response_b = await client.get("/v1/reports/anomalies?month=2026-08", headers=headers_b)
    assert response_b.json()["data"]["items"] == []


# ---------------------------------------------------------------------------
# /reports/savings-rate


async def test_savings_rate_computes_percent_of_income_saved(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    month = _current_month()
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="income",
        amount_cents=1_000_00,
        date=f"{month}-05",
    )
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="expense",
        amount_cents=700_00,
        date=f"{month}-06",
    )

    response = await client.get("/v1/reports/savings-rate?months=1", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["months"] == 1
    point = data["points"][0]
    assert point["income_cents"] == 1_000_00
    assert point["expense_cents"] == 700_00
    assert point["savings_rate_percent"] == 30


async def test_savings_rate_is_null_without_income(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    month = _current_month()
    await _create_transaction(
        client,
        headers,
        account_id=account_id,
        kind="expense",
        amount_cents=500_00,
        date=f"{month}-05",
    )

    response = await client.get("/v1/reports/savings-rate?months=1", headers=headers)
    assert response.json()["data"]["points"][0]["savings_rate_percent"] is None


async def test_savings_rate_defaults_to_12_months(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    await create_account(client, headers)

    response = await client.get("/v1/reports/savings-rate", headers=headers)
    data = response.json()["data"]
    assert data["months"] == 12
    assert len(data["points"]) == 12


async def test_savings_rate_isolated_from_other_users_data(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, email="savings-a@example.com")
    headers_b = await register_and_login(client, email="savings-b@example.com")
    account_a = await create_account(client, headers_a, initial_balance_cents=1_000_000)
    month = _current_month()
    await _create_transaction(
        client,
        headers_a,
        account_id=account_a,
        kind="income",
        amount_cents=1_000_00,
        date=f"{month}-05",
    )

    response_b = await client.get("/v1/reports/savings-rate?months=1", headers=headers_b)
    point = response_b.json()["data"]["points"][0]
    assert point["income_cents"] == 0
    assert point["savings_rate_percent"] is None


# ---------------------------------------------------------------------------
# /reports/upcoming


async def test_upcoming_includes_installments_and_recurring_sorted_by_date(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    await _create_installment_plan(client, headers, account_id, first_payment_date="2026-09-15")
    await _create_recurring_rule(client, headers, account_id, next_due_date="2026-09-20")

    response = await client.get("/v1/reports/upcoming?days=30", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["days"] == 30
    types = [item["source_type"] for item in data["items"]]
    assert "installment" in types
    assert "recurring" in types
    due_dates = [item["due_date"] for item in data["items"]]
    assert due_dates == sorted(due_dates)


async def test_upcoming_includes_credit_card_statement_and_payment(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    today = datetime.now(UTC).date()
    await create_account(
        client,
        headers,
        account_type="credit_card",
        initial_balance_cents=50_000,
        name="Tarjeta",
        statement_day=today.day,
        payment_due_day=today.day,
    )

    # El pago cae al mes siguiente (el corte es hoy, el pago no puede ser el
    # mismo día): hasta 31 días de holgura para no depender del mes exacto.
    response = await client.get("/v1/reports/upcoming?days=35", headers=headers)
    types = [item["source_type"] for item in response.json()["data"]["items"]]
    assert "card_statement" in types
    assert "card_payment" in types


async def test_upcoming_includes_active_debt_monthly_payment(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    today = datetime.now(UTC).date()
    start_date = today.replace(day=1).isoformat()
    await _create_debt(
        client,
        headers,
        principal_cents=1_000_000,
        start_date=start_date,
        monthly_payment_cents=50_000,
    )

    response = await client.get("/v1/reports/upcoming?days=35", headers=headers)
    items = response.json()["data"]["items"]
    debt_items = [item for item in items if item["source_type"] == "debt_payment"]
    assert len(debt_items) == 1
    assert debt_items[0]["amount_cents"] == 50_000


async def test_upcoming_excludes_items_beyond_days_window(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    await _create_recurring_rule(client, headers, account_id, next_due_date="2026-12-31")

    response = await client.get("/v1/reports/upcoming?days=7", headers=headers)
    assert response.json()["data"]["items"] == []


async def test_upcoming_defaults_to_30_days(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    await create_account(client, headers)

    response = await client.get("/v1/reports/upcoming", headers=headers)
    assert response.json()["data"]["days"] == 30


async def test_upcoming_isolated_from_other_users_data(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, email="upcoming-a@example.com")
    headers_b = await register_and_login(client, email="upcoming-b@example.com")
    account_a = await create_account(client, headers_a, initial_balance_cents=1_000_000)
    await _create_recurring_rule(client, headers_a, account_a, next_due_date="2026-09-20")

    response_b = await client.get("/v1/reports/upcoming?days=30", headers=headers_b)
    assert response_b.json()["data"]["items"] == []
