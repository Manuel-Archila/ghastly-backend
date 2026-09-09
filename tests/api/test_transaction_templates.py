import uuid
from datetime import UTC, datetime

from httpx import AsyncClient

from tests.api.helpers import create_account, create_category, register_and_login


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def _current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


async def _create_template(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    account_id: uuid.UUID,
    category_id: uuid.UUID | None = None,
    kind: str = "expense",
    amount_cents: int = 5_000,
    name: str = "Café",
) -> uuid.UUID:
    template_id = uuid.uuid4()
    payload: dict[str, object] = {
        "id": str(template_id),
        "name": name,
        "account_id": str(account_id),
        "kind": kind,
        "amount_cents": amount_cents,
    }
    if category_id is not None:
        payload["category_id"] = str(category_id)
    response = await client.post("/v1/transaction-templates", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return template_id


async def test_create_template_returns_zero_use_count(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)

    response = await client.post(
        "/v1/transaction-templates",
        json={
            "id": str(uuid.uuid4()),
            "name": "Café",
            "account_id": str(account_id),
            "kind": "expense",
            "amount_cents": 3_500,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["name"] == "Café"
    assert data["use_count"] == 0
    assert data["last_used_at"] is None


async def test_create_template_rejects_category_kind_mismatch(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    income_category = await create_category(client, headers, kind="income", name="Salario")

    response = await client.post(
        "/v1/transaction-templates",
        json={
            "id": str(uuid.uuid4()),
            "name": "Café",
            "account_id": str(account_id),
            "category_id": str(income_category),
            "kind": "expense",
            "amount_cents": 3_500,
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "CATEGORY_KIND_MISMATCH"


async def test_create_template_rejects_unknown_account(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    response = await client.post(
        "/v1/transaction-templates",
        json={
            "id": str(uuid.uuid4()),
            "name": "Café",
            "account_id": str(uuid.uuid4()),
            "kind": "expense",
            "amount_cents": 3_500,
        },
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "ACCOUNT_NOT_FOUND"


async def test_list_templates_orders_by_use_count_descending(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    less_used = await _create_template(client, headers, account_id=account_id, name="Menos usada")
    more_used = await _create_template(client, headers, account_id=account_id, name="Más usada")

    for _ in range(3):
        response = await client.post(
            "/v1/transactions",
            json={
                "id": str(uuid.uuid4()),
                "account_id": str(account_id),
                "kind": "expense",
                "amount_cents": 5_000,
                "date": f"{_current_month()}-05",
                "template_id": str(more_used),
            },
            headers={**headers, **_idem()},
        )
        assert response.status_code == 200, response.text

    response = await client.get("/v1/transaction-templates", headers=headers)
    assert response.status_code == 200, response.text
    ids = [t["id"] for t in response.json()["data"]]
    assert ids == [str(more_used), str(less_used)]
    assert response.json()["data"][0]["use_count"] == 3
    assert response.json()["data"][0]["last_used_at"] is not None


async def test_creating_transaction_without_template_id_does_not_touch_templates(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    template_id = await _create_template(client, headers, account_id=account_id)

    response = await client.post(
        "/v1/transactions",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "amount_cents": 5_000,
            "date": f"{_current_month()}-05",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text

    response = await client.get("/v1/transaction-templates", headers=headers)
    assert response.json()["data"][0]["id"] == str(template_id)
    assert response.json()["data"][0]["use_count"] == 0


async def test_creating_transaction_with_unknown_template_id_fails_and_rolls_back(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)

    response = await client.post(
        "/v1/transactions",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "amount_cents": 5_000,
            "date": f"{_current_month()}-05",
            "template_id": str(uuid.uuid4()),
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "TEMPLATE_NOT_FOUND"

    accounts = await client.get("/v1/accounts", headers=headers)
    balances = {a["id"]: a["current_balance_cents"] for a in accounts.json()["data"]}
    assert balances[str(account_id)] == 1_000_000  # el gasto no se aplicó


async def test_delete_template_removes_it_from_list(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    template_id = await _create_template(client, headers, account_id=account_id)

    response = await client.delete(f"/v1/transaction-templates/{template_id}", headers=headers)
    assert response.status_code == 200, response.text

    response = await client.get("/v1/transaction-templates", headers=headers)
    assert response.json()["data"] == []


async def test_delete_unknown_template_returns_404(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    response = await client.delete(f"/v1/transaction-templates/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "TEMPLATE_NOT_FOUND"


async def test_cross_user_cannot_see_or_delete_template(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, email="template-a@example.com")
    headers_b = await register_and_login(client, email="template-b@example.com")
    account_a = await create_account(client, headers_a, initial_balance_cents=1_000_000)
    template_id = await _create_template(client, headers_a, account_id=account_a)

    list_response = await client.get("/v1/transaction-templates", headers=headers_b)
    assert list_response.json()["data"] == []

    delete_response = await client.delete(
        f"/v1/transaction-templates/{template_id}", headers=headers_b
    )
    assert delete_response.status_code == 404
    assert delete_response.json()["data"]["code"] == "TEMPLATE_NOT_FOUND"
