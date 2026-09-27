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


async def test_get_template_by_id(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    template_id = await _create_template(client, headers, account_id=account_id, name="Café")

    response = await client.get(f"/v1/transaction-templates/{template_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "Café"

    missing = await client.get(f"/v1/transaction-templates/{uuid.uuid4()}", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["data"]["code"] == "TEMPLATE_NOT_FOUND"


async def test_patch_template_updates_fields_and_keeps_use_count(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    other_account = await create_account(client, headers, name="Otra")
    category_id = await create_category(client, headers, name="Comida")
    template_id = await _create_template(
        client, headers, account_id=account_id, category_id=category_id
    )

    response = await client.patch(
        f"/v1/transaction-templates/{template_id}",
        json={"name": "Almuerzo", "amount_cents": 7_500, "account_id": str(other_account)},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert (data["name"], data["amount_cents"]) == ("Almuerzo", 7_500)
    assert data["account_id"] == str(other_account)
    assert data["category_id"] == str(category_id)  # lo omitido no se toca

    cleared = await client.patch(
        f"/v1/transaction-templates/{template_id}", json={"category_id": None}, headers=headers
    )
    assert cleared.json()["data"]["category_id"] is None


async def test_patch_template_validates_references(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    expense_cat = await create_category(client, headers, name="Comida")
    income_cat = await create_category(client, headers, name="Sueldo", kind="income")
    template_id = await _create_template(
        client, headers, account_id=account_id, category_id=expense_cat
    )
    url = f"/v1/transaction-templates/{template_id}"

    wrong_category = await client.patch(url, json={"category_id": str(income_cat)}, headers=headers)
    assert wrong_category.status_code == 422
    assert wrong_category.json()["data"]["code"] == "CATEGORY_KIND_MISMATCH"

    # Cambiar solo el tipo deja la categoría vieja incompatible.
    wrong_kind = await client.patch(url, json={"kind": "income"}, headers=headers)
    assert wrong_kind.status_code == 422
    assert wrong_kind.json()["data"]["code"] == "CATEGORY_KIND_MISMATCH"

    both = await client.patch(
        url, json={"kind": "income", "category_id": str(income_cat)}, headers=headers
    )
    assert both.status_code == 200

    unknown_account = await client.patch(
        url, json={"account_id": str(uuid.uuid4())}, headers=headers
    )
    assert unknown_account.status_code == 404
    assert unknown_account.json()["data"]["code"] == "ACCOUNT_NOT_FOUND"

    for field in ("name", "account_id", "kind", "amount_cents"):
        null_field = await client.patch(url, json={field: None}, headers=headers)
        assert null_field.status_code == 422, field


async def test_cross_user_cannot_get_or_patch_template(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, email="template-a@example.com")
    headers_b = await register_and_login(client, email="template-b@example.com")
    account_a = await create_account(client, headers_a, initial_balance_cents=1_000_000)
    account_b = await create_account(client, headers_b, initial_balance_cents=1_000_000)
    template_id = await _create_template(client, headers_a, account_id=account_a, name="Mío")
    url = f"/v1/transaction-templates/{template_id}"

    for response in (
        await client.get(url, headers=headers_b),
        await client.patch(url, json={"name": "Robado"}, headers=headers_b),
    ):
        assert response.status_code == 404
        assert response.json()["data"]["code"] == "TEMPLATE_NOT_FOUND"

    # Y no se puede apuntar la plantilla propia a la cuenta de otro usuario.
    own = await _create_template(client, headers_b, account_id=account_b)
    stolen_account = await client.patch(
        f"/v1/transaction-templates/{own}", json={"account_id": str(account_a)}, headers=headers_b
    )
    assert stolen_account.status_code == 404
    assert stolen_account.json()["data"]["code"] == "ACCOUNT_NOT_FOUND"

    untouched = await client.get(url, headers=headers_a)
    assert untouched.json()["data"]["name"] == "Mío"
