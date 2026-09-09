import uuid
from datetime import UTC, datetime

from httpx import AsyncClient

from domain.dates import add_months_clamped
from tests.api.helpers import create_account, register_and_login


async def _create_expense(
    client: AsyncClient,
    headers: dict[str, str],
    account_id: uuid.UUID,
    amount_cents: int,
    date: str,
) -> uuid.UUID:
    transaction_id = uuid.uuid4()
    response = await client.post(
        "/v1/transactions",
        json={
            "id": str(transaction_id),
            "account_id": str(account_id),
            "kind": "expense",
            "amount_cents": amount_cents,
            "date": date,
        },
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 200, response.text
    return transaction_id


async def test_create_and_get_account(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=100_000)

    response = await client.get(f"/v1/accounts/{account_id}", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["current_balance_cents"] == 100_000
    assert data["initial_balance_cents"] == 100_000


async def test_list_accounts_excludes_archived_by_default(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    await client.delete(f"/v1/accounts/{account_id}?force=true", headers=headers)

    response = await client.get("/v1/accounts", headers=headers)
    assert response.json()["data"] == []

    response = await client.get("/v1/accounts?include_archived=true", headers=headers)
    assert len(response.json()["data"]) == 1


async def test_cross_user_cannot_read_account(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, "a@example.com")
    headers_b = await register_and_login(client, "b@example.com")
    account_id = await create_account(client, headers_a)

    response = await client.get(f"/v1/accounts/{account_id}", headers=headers_b)
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "ACCOUNT_NOT_FOUND"


async def test_archive_with_nonzero_balance_requires_force(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=500)

    response = await client.delete(f"/v1/accounts/{account_id}", headers=headers)
    assert response.status_code == 409
    assert response.json()["data"]["code"] == "ACCOUNT_HAS_BALANCE"

    response = await client.delete(f"/v1/accounts/{account_id}?force=true", headers=headers)
    assert response.status_code == 200


async def test_adjust_creates_reconciled_transaction_case_09(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=10_000)

    response = await client.post(
        f"/v1/accounts/{account_id}/adjust",
        json={"real_balance_cents": 12_000, "note": "Conciliación de septiembre"},
        headers=headers,
    )
    assert response.status_code == 200
    txn = response.json()["data"]
    assert txn["kind"] == "income"
    assert txn["amount_cents"] == 2_000
    assert txn["is_reconciled"] is True

    account = await client.get(f"/v1/accounts/{account_id}", headers=headers)
    assert account.json()["data"]["current_balance_cents"] == 12_000


async def test_adjust_with_no_difference_is_rejected(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=5_000)

    response = await client.post(
        f"/v1/accounts/{account_id}/adjust",
        json={"real_balance_cents": 5_000, "note": "sin cambios"},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "NO_ADJUSTMENT_NEEDED"


async def test_credit_card_expense_increases_debt(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, account_type="credit_card")

    await client.post(
        "/v1/transactions",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "amount_cents": 3_000,
            "date": "2026-09-04",
        },
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    account = await client.get(f"/v1/accounts/{account_id}", headers=headers)
    assert account.json()["data"]["current_balance_cents"] == 3_000


async def test_recalculate_matches_incremental_balance(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000)

    for _ in range(3):
        await client.post(
            "/v1/transactions",
            json={
                "id": str(uuid.uuid4()),
                "account_id": str(account_id),
                "kind": "expense",
                "amount_cents": 100,
                "date": "2026-09-04",
            },
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )

    before = await client.get(f"/v1/accounts/{account_id}", headers=headers)
    recalculated = await client.post(f"/v1/accounts/{account_id}/recalculate", headers=headers)
    assert (
        recalculated.json()["data"]["current_balance_cents"]
        == before.json()["data"]["current_balance_cents"]
        == 700
    )


# ---------------------------------------------------------------------------
# GET /accounts/{id}/statement


async def test_statement_current_cycle_computes_spend_dates_and_balance(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    today = datetime.now(UTC).date()
    one_month_ago = add_months_clamped(today, -1)
    account_id = await create_account(
        client,
        headers,
        account_type="credit_card",
        statement_day=today.day,
        payment_due_day=today.day,
    )
    await _create_expense(client, headers, account_id, 50_000, today.isoformat())
    await _create_expense(client, headers, account_id, 30_000, one_month_ago.isoformat())

    response = await client.get(f"/v1/accounts/{account_id}/statement", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["cycle"] == "current"
    assert data["statement_date"] == today.isoformat()
    assert data["spend_cents"] == 50_000  # el gasto de hace un mes no entra en este corte
    assert data["balance_cents"] == 80_000  # pero sí en el saldo acumulado
    assert data["minimum_cents"] is None


async def test_statement_previous_cycle_and_specific_month_agree(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    today = datetime.now(UTC).date()
    one_month_ago = add_months_clamped(today, -1)
    account_id = await create_account(
        client,
        headers,
        account_type="credit_card",
        statement_day=today.day,
        payment_due_day=today.day,
    )
    await _create_expense(client, headers, account_id, 30_000, one_month_ago.isoformat())

    previous = await client.get(
        f"/v1/accounts/{account_id}/statement?cycle=previous", headers=headers
    )
    assert previous.status_code == 200, previous.text
    previous_data = previous.json()["data"]
    assert previous_data["cycle"] == "previous"
    assert previous_data["statement_date"] == one_month_ago.isoformat()
    assert previous_data["spend_cents"] == 30_000

    specific = await client.get(
        f"/v1/accounts/{account_id}/statement?cycle={one_month_ago.year:04d}-{one_month_ago.month:02d}",
        headers=headers,
    )
    assert specific.json()["data"]["statement_date"] == previous_data["statement_date"]
    assert specific.json()["data"]["spend_cents"] == 30_000


async def test_statement_computes_minimum_from_configured_percent(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    today = datetime.now(UTC).date()
    account_id = await create_account(
        client,
        headers,
        account_type="credit_card",
        statement_day=today.day,
        payment_due_day=today.day,
        minimum_payment_percent="5.00",
    )
    await _create_expense(client, headers, account_id, 100_000, today.isoformat())

    response = await client.get(f"/v1/accounts/{account_id}/statement", headers=headers)
    assert response.json()["data"]["minimum_cents"] == 5_000


async def test_statement_rejects_non_credit_card_account(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, account_type="checking")

    response = await client.get(f"/v1/accounts/{account_id}/statement", headers=headers)
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "ACCOUNT_NOT_CREDIT_CARD"


async def test_statement_rejects_credit_card_without_cycle_configured(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, account_type="credit_card")

    response = await client.get(f"/v1/accounts/{account_id}/statement", headers=headers)
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "ACCOUNT_CYCLE_NOT_CONFIGURED"


async def test_statement_rejects_invalid_cycle_param(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    today = datetime.now(UTC).date()
    account_id = await create_account(
        client,
        headers,
        account_type="credit_card",
        statement_day=today.day,
        payment_due_day=today.day,
    )

    response = await client.get(
        f"/v1/accounts/{account_id}/statement?cycle=garbage", headers=headers
    )
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "INVALID_CYCLE"


async def test_cross_user_cannot_read_statement(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, "statement-a@example.com")
    headers_b = await register_and_login(client, "statement-b@example.com")
    today = datetime.now(UTC).date()
    account_id = await create_account(
        client,
        headers_a,
        account_type="credit_card",
        statement_day=today.day,
        payment_due_day=today.day,
    )

    response = await client.get(f"/v1/accounts/{account_id}/statement", headers=headers_b)
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "ACCOUNT_NOT_FOUND"
