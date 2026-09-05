import uuid

from httpx import AsyncClient

from tests.api.helpers import create_account, register_and_login


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
