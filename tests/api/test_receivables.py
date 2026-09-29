import uuid
from datetime import UTC, datetime

from httpx import AsyncClient

from tests.api.helpers import create_account, default_expense_category, register_and_login


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def _current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


async def _create_expense(
    client: AsyncClient, headers: dict[str, str], account_id: uuid.UUID, amount_cents: int
) -> uuid.UUID:
    transaction_id = uuid.uuid4()
    response = await client.post(
        "/v1/transactions",
        json={
            "id": str(transaction_id),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "amount_cents": amount_cents,
            "date": f"{_current_month()}-05",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text
    return transaction_id


async def _create_receivable(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    transaction_id: uuid.UUID,
    amount_cents: int,
    counterparty: str = "Ana",
) -> uuid.UUID:
    receivable_id = uuid.uuid4()
    response = await client.post(
        "/v1/receivables",
        json={
            "id": str(receivable_id),
            "transaction_id": str(transaction_id),
            "counterparty": counterparty,
            "amount_cents": amount_cents,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return receivable_id


async def test_create_receivable_returns_pending_status(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    expense_id = await _create_expense(client, headers, account_id, 200_000)

    response = await client.post(
        "/v1/receivables",
        json={
            "id": str(uuid.uuid4()),
            "transaction_id": str(expense_id),
            "counterparty": "Ana",
            "amount_cents": 100_000,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "pending"
    assert data["amount_cents"] == 100_000
    assert data["counterparty"] == "Ana"
    assert data["settled_at"] is None
    assert data["settlement_transaction_id"] is None


async def test_create_receivable_rejects_income_transaction(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    income_id = uuid.uuid4()
    response = await client.post(
        "/v1/transactions",
        json={
            "id": str(income_id),
            "account_id": str(account_id),
            "kind": "income",
            "amount_cents": 100_000,
            "date": f"{_current_month()}-05",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text

    response = await client.post(
        "/v1/receivables",
        json={
            "id": str(uuid.uuid4()),
            "transaction_id": str(income_id),
            "counterparty": "Ana",
            "amount_cents": 50_000,
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "RECEIVABLE_REQUIRES_EXPENSE_TRANSACTION"


async def test_create_receivable_rejects_amount_exceeding_transaction_total(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    expense_id = await _create_expense(client, headers, account_id, 100_000)

    response = await client.post(
        "/v1/receivables",
        json={
            "id": str(uuid.uuid4()),
            "transaction_id": str(expense_id),
            "counterparty": "Ana",
            "amount_cents": 150_000,
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "RECEIVABLE_EXCEEDS_TRANSACTION_AMOUNT"


async def test_create_multiple_receivables_against_same_expense_split_three_ways(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    expense_id = await _create_expense(client, headers, account_id, 300_000)

    await _create_receivable(
        client, headers, transaction_id=expense_id, amount_cents=100_000, counterparty="Ana"
    )
    await _create_receivable(
        client, headers, transaction_id=expense_id, amount_cents=100_000, counterparty="Beto"
    )

    # una tercera que ya no cabe (100_000 + 100_000 + 150_000 > 300_000)
    response = await client.post(
        "/v1/receivables",
        json={
            "id": str(uuid.uuid4()),
            "transaction_id": str(expense_id),
            "counterparty": "Caro",
            "amount_cents": 150_000,
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "RECEIVABLE_EXCEEDS_TRANSACTION_AMOUNT"

    response = await client.get("/v1/receivables", headers=headers)
    assert len(response.json()["data"]) == 2


async def test_list_receivables_returns_all_for_user(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    expense_id = await _create_expense(client, headers, account_id, 200_000)
    await _create_receivable(client, headers, transaction_id=expense_id, amount_cents=100_000)

    response = await client.get("/v1/receivables", headers=headers)
    assert response.status_code == 200, response.text
    assert len(response.json()["data"]) == 1


async def test_settle_creates_income_transaction_and_updates_balance(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    expense_id = await _create_expense(client, headers, account_id, 200_000)
    receivable_id = await _create_receivable(
        client, headers, transaction_id=expense_id, amount_cents=100_000
    )

    settlement_id = uuid.uuid4()
    response = await client.post(
        f"/v1/receivables/{receivable_id}/settle",
        json={
            "id": str(settlement_id),
            "account_id": str(account_id),
            "date": f"{_current_month()}-10",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "settled"
    assert data["settled_at"] is not None
    assert data["settlement_transaction_id"] == str(settlement_id)

    txn = await client.get(f"/v1/transactions/{settlement_id}", headers=headers)
    assert txn.status_code == 200, txn.text
    assert txn.json()["data"]["kind"] == "income"
    assert txn.json()["data"]["amount_cents"] == 100_000

    account = await client.get("/v1/accounts", headers=headers)
    balances = {a["id"]: a["current_balance_cents"] for a in account.json()["data"]}
    # 1_000_000 - 200_000 (gasto) + 100_000 (cobro) = 900_000
    assert balances[str(account_id)] == 900_000


async def test_settle_twice_is_rejected(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    expense_id = await _create_expense(client, headers, account_id, 200_000)
    receivable_id = await _create_receivable(
        client, headers, transaction_id=expense_id, amount_cents=100_000
    )

    response = await client.post(
        f"/v1/receivables/{receivable_id}/settle",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "date": f"{_current_month()}-10",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text

    response = await client.post(
        f"/v1/receivables/{receivable_id}/settle",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "date": f"{_current_month()}-11",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 409
    assert response.json()["data"]["code"] == "RECEIVABLE_ALREADY_SETTLED"


async def test_settle_retried_with_same_idempotency_key_does_not_duplicate(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    expense_id = await _create_expense(client, headers, account_id, 200_000)
    receivable_id = await _create_receivable(
        client, headers, transaction_id=expense_id, amount_cents=100_000
    )

    body = {
        "id": str(uuid.uuid4()),
        "account_id": str(account_id),
        "date": f"{_current_month()}-10",
    }
    idem_headers = {**headers, **_idem()}

    first = await client.post(
        f"/v1/receivables/{receivable_id}/settle", json=body, headers=idem_headers
    )
    assert first.status_code == 200, first.text

    second = await client.post(
        f"/v1/receivables/{receivable_id}/settle", json=body, headers=idem_headers
    )
    assert second.status_code == 200, second.text
    assert second.json() == first.json()

    account = await client.get("/v1/accounts", headers=headers)
    balances = {a["id"]: a["current_balance_cents"] for a in account.json()["data"]}
    assert balances[str(account_id)] == 900_000  # no se aplicó dos veces


async def test_get_receivable_not_found(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    response = await client.get(f"/v1/receivables/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "RECEIVABLE_NOT_FOUND"


async def test_cross_user_cannot_read_or_settle_receivable(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, email="receivable-a@example.com")
    headers_b = await register_and_login(client, email="receivable-b@example.com")
    account_a = await create_account(client, headers_a, initial_balance_cents=1_000_000)
    account_b = await create_account(client, headers_b, initial_balance_cents=1_000_000)
    expense_id = await _create_expense(client, headers_a, account_a, 200_000)
    receivable_id = await _create_receivable(
        client, headers_a, transaction_id=expense_id, amount_cents=100_000
    )

    get_response = await client.get(f"/v1/receivables/{receivable_id}", headers=headers_b)
    assert get_response.status_code == 404
    assert get_response.json()["data"]["code"] == "RECEIVABLE_NOT_FOUND"

    settle_response = await client.post(
        f"/v1/receivables/{receivable_id}/settle",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_b),
            "date": f"{_current_month()}-10",
        },
        headers={**headers_b, **_idem()},
    )
    assert settle_response.status_code == 404
    assert settle_response.json()["data"]["code"] == "RECEIVABLE_NOT_FOUND"

    list_response = await client.get("/v1/receivables", headers=headers_b)
    assert list_response.json()["data"] == []


async def test_cannot_create_receivable_on_another_users_transaction(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, email="txn-a@example.com")
    headers_b = await register_and_login(client, email="txn-b@example.com")
    account_a = await create_account(client, headers_a, initial_balance_cents=1_000_000)
    expense_id = await _create_expense(client, headers_a, account_a, 200_000)

    response = await client.post(
        "/v1/receivables",
        json={
            "id": str(uuid.uuid4()),
            "transaction_id": str(expense_id),
            "counterparty": "Ana",
            "amount_cents": 100_000,
        },
        headers=headers_b,
    )
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "TRANSACTION_NOT_FOUND"


async def test_update_receivable_changes_counterparty_and_amount(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    expense_id = await _create_expense(client, headers, account_id, 200_000)
    receivable_id = await _create_receivable(
        client, headers, transaction_id=expense_id, amount_cents=100_000
    )

    response = await client.patch(
        f"/v1/receivables/{receivable_id}",
        json={"counterparty": "Ana María", "amount_cents": 150_000},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["counterparty"] == "Ana María"
    assert response.json()["data"]["amount_cents"] == 150_000

    # Sigue contando en "por cobrar" con el monto nuevo.
    dashboard = await client.get("/v1/reports/dashboard", headers=headers)
    assert dashboard.json()["data"]["receivable_cents"] == 150_000


async def test_update_receivable_respects_the_sum_across_people(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    expense_id = await _create_expense(client, headers, account_id, 200_000)
    ana = await _create_receivable(client, headers, transaction_id=expense_id, amount_cents=100_000)
    await _create_receivable(
        client, headers, transaction_id=expense_id, amount_cents=80_000, counterparty="Luis"
    )

    too_much = await client.patch(
        f"/v1/receivables/{ana}", json={"amount_cents": 130_000}, headers=headers
    )
    assert too_much.status_code == 422
    assert too_much.json()["data"]["code"] == "RECEIVABLE_EXCEEDS_TRANSACTION_AMOUNT"

    # Su propio monto actual no cuenta contra sí mismo.
    fits = await client.patch(
        f"/v1/receivables/{ana}", json={"amount_cents": 120_000}, headers=headers
    )
    assert fits.status_code == 200

    null_amount = await client.patch(
        f"/v1/receivables/{ana}", json={"amount_cents": None}, headers=headers
    )
    assert null_amount.status_code == 422


async def test_delete_receivable_frees_the_amount_and_hides_it(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    expense_id = await _create_expense(client, headers, account_id, 200_000)
    receivable_id = await _create_receivable(
        client, headers, transaction_id=expense_id, amount_cents=200_000
    )

    response = await client.delete(f"/v1/receivables/{receivable_id}", headers=headers)
    assert response.status_code == 200, response.text

    assert (
        await client.get(f"/v1/receivables/{receivable_id}", headers=headers)
    ).status_code == 404
    assert (await client.get("/v1/receivables", headers=headers)).json()["data"] == []
    dashboard = await client.get("/v1/reports/dashboard", headers=headers)
    assert dashboard.json()["data"]["receivable_cents"] == 0

    # El cupo del gasto quedó libre otra vez.
    await _create_receivable(client, headers, transaction_id=expense_id, amount_cents=200_000)

    pull = await client.get("/v1/sync/pull?since=0", headers=headers)
    ops = [
        c["op"]
        for c in pull.json()["data"]["changes"]
        if c["entity_type"] == "receivable" and c["entity_id"] == str(receivable_id)
    ]
    assert ops == ["upsert", "delete"]


async def test_settled_receivable_cannot_be_edited_or_deleted(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    expense_id = await _create_expense(client, headers, account_id, 200_000)
    receivable_id = await _create_receivable(
        client, headers, transaction_id=expense_id, amount_cents=100_000
    )
    await client.post(
        f"/v1/receivables/{receivable_id}/settle",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "date": f"{_current_month()}-10",
        },
        headers={**headers, **_idem()},
    )

    patched = await client.patch(
        f"/v1/receivables/{receivable_id}", json={"counterparty": "Otro"}, headers=headers
    )
    deleted = await client.delete(f"/v1/receivables/{receivable_id}", headers=headers)
    for response in (patched, deleted):
        assert response.status_code == 409
        assert response.json()["data"]["code"] == "RECEIVABLE_ALREADY_SETTLED"


async def test_cross_user_cannot_edit_or_delete_receivable(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, email="receivable-a@example.com")
    headers_b = await register_and_login(client, email="receivable-b@example.com")
    account_a = await create_account(client, headers_a, initial_balance_cents=1_000_000)
    expense_id = await _create_expense(client, headers_a, account_a, 200_000)
    receivable_id = await _create_receivable(
        client, headers_a, transaction_id=expense_id, amount_cents=100_000
    )

    patched = await client.patch(
        f"/v1/receivables/{receivable_id}", json={"counterparty": "X"}, headers=headers_b
    )
    deleted = await client.delete(f"/v1/receivables/{receivable_id}", headers=headers_b)
    for response in (patched, deleted):
        assert response.status_code == 404
        assert response.json()["data"]["code"] == "RECEIVABLE_NOT_FOUND"

    still_there = await client.get(f"/v1/receivables/{receivable_id}", headers=headers_a)
    assert still_there.json()["data"]["counterparty"] == "Ana"
