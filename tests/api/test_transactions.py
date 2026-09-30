import uuid
from decimal import Decimal

from httpx import AsyncClient

from tests.api.helpers import (
    create_account,
    create_category,
    default_expense_category,
    default_income_category,
    register_and_login,
)


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def test_create_expense_reduces_balance(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=10_000)

    response = await client.post(
        "/v1/transactions",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "amount_cents": 1_500,
            "date": "2026-09-04",
            "description": "Almuerzo",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["warning"] is None

    account = await client.get(f"/v1/accounts/{account_id}", headers=headers)
    assert account.json()["data"]["current_balance_cents"] == 8_500


async def test_create_transaction_requires_idempotency_key(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)

    response = await client.post(
        "/v1/transactions",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "amount_cents": 100,
            "date": "2026-09-04",
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


async def test_retrying_same_idempotency_key_does_not_duplicate(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=10_000)
    key = {"Idempotency-Key": str(uuid.uuid4())}
    body = {
        "id": str(uuid.uuid4()),
        "account_id": str(account_id),
        "kind": "expense",
        "category_id": str(await default_expense_category(client, headers)),
        "amount_cents": 1_000,
        "date": "2026-09-04",
    }

    first = await client.post("/v1/transactions", json=body, headers={**headers, **key})
    second = await client.post("/v1/transactions", json=body, headers={**headers, **key})
    assert first.json() == second.json()

    account = await client.get(f"/v1/accounts/{account_id}", headers=headers)
    # Si hubiera duplicado, el saldo habría bajado dos veces (8,000 en vez de 9,000).
    assert account.json()["data"]["current_balance_cents"] == 9_000


async def test_reusing_idempotency_key_with_different_body_is_conflict(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=10_000)
    key = {"Idempotency-Key": str(uuid.uuid4())}

    await client.post(
        "/v1/transactions",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "amount_cents": 100,
            "date": "2026-09-04",
        },
        headers={**headers, **key},
    )
    response = await client.post(
        "/v1/transactions",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "amount_cents": 999,
            "date": "2026-09-04",
        },
        headers={**headers, **key},
    )
    assert response.status_code == 409
    assert response.json()["data"]["code"] == "IDEMPOTENCY_KEY_REUSED"


async def test_duplicate_warning_within_window(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=10_000)
    body = {
        "account_id": str(account_id),
        "kind": "expense",
        "category_id": str(await default_expense_category(client, headers)),
        "amount_cents": 250,
        "date": "2026-09-04",
    }

    await client.post(
        "/v1/transactions", json={**body, "id": str(uuid.uuid4())}, headers={**headers, **_idem()}
    )
    second = await client.post(
        "/v1/transactions", json={**body, "id": str(uuid.uuid4())}, headers={**headers, **_idem()}
    )
    assert second.json()["data"]["warning"]["code"] == "POSSIBLE_DUPLICATE"


async def test_transfer_excludes_from_stats_case_01(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    checking = await create_account(client, headers, initial_balance_cents=10_000, name="Checking")
    savings = await create_account(client, headers, initial_balance_cents=0, name="Ahorros")

    response = await client.post(
        "/v1/transactions/transfer",
        json={
            "out_transaction_id": str(uuid.uuid4()),
            "in_transaction_id": str(uuid.uuid4()),
            "from_account_id": str(checking),
            "to_account_id": str(savings),
            "amount_cents": 3_000,
            "date": "2026-09-04",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text

    checking_acc = await client.get(f"/v1/accounts/{checking}", headers=headers)
    savings_acc = await client.get(f"/v1/accounts/{savings}", headers=headers)
    assert checking_acc.json()["data"]["current_balance_cents"] == 7_000
    assert savings_acc.json()["data"]["current_balance_cents"] == 3_000

    stats = await client.get("/v1/transactions/stats", headers=headers)
    # La transferencia no debe contar ni como ingreso ni como gasto.
    assert stats.json()["data"]["count"] == 0
    assert stats.json()["data"]["total_income_cents"] == 0
    assert stats.json()["data"]["total_expense_cents"] == 0


async def test_transfer_same_account_rejected(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)

    response = await client.post(
        "/v1/transactions/transfer",
        json={
            "out_transaction_id": str(uuid.uuid4()),
            "in_transaction_id": str(uuid.uuid4()),
            "from_account_id": str(account_id),
            "to_account_id": str(account_id),
            "amount_cents": 100,
            "date": "2026-09-04",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "TRANSFER_SAME_ACCOUNT"


async def test_refund_creates_income_linked_to_original_case_02(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=10_000)
    category_id = await create_category(client, headers, name="Ropa")

    expense_id = uuid.uuid4()
    await client.post(
        "/v1/transactions",
        json={
            "id": str(expense_id),
            "account_id": str(account_id),
            "category_id": str(category_id),
            "kind": "expense",
            "amount_cents": 2_000,
            "date": "2026-09-04",
        },
        headers={**headers, **_idem()},
    )

    refund = await client.post(
        f"/v1/transactions/{expense_id}/refund",
        json={"id": str(uuid.uuid4())},
        headers={**headers, **_idem()},
    )
    assert refund.status_code == 200, refund.text
    data = refund.json()["data"]
    assert data["kind"] == "income"
    assert data["amount_cents"] == 2_000
    assert data["refund_of_id"] == str(expense_id)
    assert data["category_id"] == str(category_id)

    account = await client.get(f"/v1/accounts/{account_id}", headers=headers)
    assert account.json()["data"]["current_balance_cents"] == 10_000  # se revirtió el gasto


async def test_refund_requires_expense(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)

    income_id = uuid.uuid4()
    await client.post(
        "/v1/transactions",
        json={
            "id": str(income_id),
            "account_id": str(account_id),
            "kind": "income",
            "category_id": str(await default_income_category(client, headers)),
            "amount_cents": 500,
            "date": "2026-09-04",
        },
        headers={**headers, **_idem()},
    )
    response = await client.post(
        f"/v1/transactions/{income_id}/refund",
        json={"id": str(uuid.uuid4())},
        headers={**headers, **_idem()},
    )
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "REFUND_REQUIRES_EXPENSE"


async def test_usd_purchase_freezes_fx_rate_case_04(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, currency="USD")

    response = await client.post(
        "/v1/transactions",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "amount_cents": 10_000,
            "currency": "USD",
            "fx_rate": "7.85",
            "date": "2026-09-04",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]["transaction"]
    assert Decimal(data["fx_rate"]) == Decimal("7.85")
    assert data["base_amount_cents"] == 78_500


async def test_usd_purchase_without_rate_and_no_fallback_fails(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, currency="USD")

    response = await client.post(
        "/v1/transactions",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "amount_cents": 10_000,
            "currency": "USD",
            "date": "2026-09-04",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "FX_RATE_REQUIRED"


async def test_soft_delete_and_restore(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000)
    txn_id = uuid.uuid4()
    await client.post(
        "/v1/transactions",
        json={
            "id": str(txn_id),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "amount_cents": 100,
            "date": "2026-09-04",
        },
        headers={**headers, **_idem()},
    )

    delete = await client.delete(f"/v1/transactions/{txn_id}", headers=headers)
    assert delete.status_code == 200

    get_after_delete = await client.get(f"/v1/transactions/{txn_id}", headers=headers)
    assert get_after_delete.status_code == 404

    restore = await client.post(f"/v1/transactions/{txn_id}/restore", headers=headers)
    assert restore.status_code == 200

    get_after_restore = await client.get(f"/v1/transactions/{txn_id}", headers=headers)
    assert get_after_restore.status_code == 200


async def test_cross_user_cannot_read_transaction(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, "a@example.com")
    headers_b = await register_and_login(client, "b@example.com")
    account_id = await create_account(client, headers_a)
    txn_id = uuid.uuid4()
    await client.post(
        "/v1/transactions",
        json={
            "id": str(txn_id),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers_a)),
            "amount_cents": 100,
            "date": "2026-09-04",
        },
        headers={**headers_a, **_idem()},
    )

    response = await client.get(f"/v1/transactions/{txn_id}", headers=headers_b)
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "TRANSACTION_NOT_FOUND"


async def test_cursor_pagination(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=100_000)

    for day in range(1, 6):
        await client.post(
            "/v1/transactions",
            json={
                "id": str(uuid.uuid4()),
                "account_id": str(account_id),
                "kind": "expense",
                "category_id": str(await default_expense_category(client, headers)),
                "amount_cents": 100,
                "date": f"2026-09-0{day}",
            },
            headers={**headers, **_idem()},
        )

    page1 = await client.get("/v1/transactions?limit=2", headers=headers)
    body1 = page1.json()["data"]
    assert len(body1["items"]) == 2
    assert body1["next_cursor"] is not None
    # Orden descendente por fecha: el más reciente primero.
    assert body1["items"][0]["date"] == "2026-09-05"

    page2 = await client.get(
        f"/v1/transactions?limit=2&cursor={body1['next_cursor']}", headers=headers
    )
    body2 = page2.json()["data"]
    assert len(body2["items"]) == 2
    seen_dates = {i["date"] for i in body1["items"] + body2["items"]}
    assert "2026-09-05" in seen_dates and "2026-09-04" in seen_dates


async def test_list_filters_by_text_search_across_description_merchant_notes(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=100_000)

    await client.post(
        "/v1/transactions",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "amount_cents": 100,
            "date": "2026-09-01",
            "description": "Almuerzo con el equipo",
        },
        headers={**headers, **_idem()},
    )
    await client.post(
        "/v1/transactions",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "amount_cents": 200,
            "date": "2026-09-02",
            "merchant": "Supermercado La Torre",
        },
        headers={**headers, **_idem()},
    )
    await client.post(
        "/v1/transactions",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "amount_cents": 300,
            "date": "2026-09-03",
            "notes": "pendiente de reembolso del equipo",
        },
        headers={**headers, **_idem()},
    )

    response = await client.get("/v1/transactions?q=equipo", headers=headers)
    assert response.status_code == 200, response.text
    amounts = {t["amount_cents"] for t in response.json()["data"]["items"]}
    assert amounts == {100, 300}

    response = await client.get("/v1/transactions?q=torre", headers=headers)
    amounts = {t["amount_cents"] for t in response.json()["data"]["items"]}
    assert amounts == {200}

    response = await client.get("/v1/transactions?q=nada-que-coincida", headers=headers)
    assert response.json()["data"]["items"] == []


async def test_list_filters_by_any_matching_tag(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=100_000)

    await client.post(
        "/v1/transactions",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "amount_cents": 100,
            "date": "2026-09-01",
            "tags": ["viaje", "trabajo"],
        },
        headers={**headers, **_idem()},
    )
    await client.post(
        "/v1/transactions",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "amount_cents": 200,
            "date": "2026-09-02",
            "tags": ["personal"],
        },
        headers={**headers, **_idem()},
    )
    await client.post(
        "/v1/transactions",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "amount_cents": 300,
            "date": "2026-09-03",
        },
        headers={**headers, **_idem()},
    )

    response = await client.get("/v1/transactions?tags=viaje", headers=headers)
    assert response.status_code == 200, response.text
    amounts = {t["amount_cents"] for t in response.json()["data"]["items"]}
    assert amounts == {100}

    # cualquiera de los tags pedidos hace match (overlap, no intersección total)
    response = await client.get("/v1/transactions?tags=viaje&tags=personal", headers=headers)
    amounts = {t["amount_cents"] for t in response.json()["data"]["items"]}
    assert amounts == {100, 200}

    response = await client.get("/v1/transactions?tags=inexistente", headers=headers)
    assert response.json()["data"]["items"] == []
