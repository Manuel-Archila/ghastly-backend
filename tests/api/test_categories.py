import uuid

from httpx import AsyncClient

from tests.api.helpers import (
    create_account,
    create_category,
    default_expense_category,
    default_income_category,
    register_and_login,
)


async def test_seed_is_idempotent(client: AsyncClient) -> None:
    headers = await register_and_login(client)

    first = await client.post("/v1/categories/seed", headers=headers)
    assert first.status_code == 200
    created_first = len(first.json()["data"])
    assert created_first > 0

    second = await client.post("/v1/categories/seed", headers=headers)
    assert second.json()["data"] == []  # nada que crear de nuevo

    listing = await client.get("/v1/categories", headers=headers)
    top_level = listing.json()["data"]
    assert len(top_level) == created_first - sum(len(c["children"]) for c in top_level)


async def test_seed_nests_servicios_children(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    await client.post("/v1/categories/seed", headers=headers)

    listing = await client.get("/v1/categories", headers=headers)
    servicios = next(c for c in listing.json()["data"] if c["name"] == "Servicios")
    child_names = {c["name"] for c in servicios["children"]}
    assert child_names == {"Luz", "Agua", "Internet", "Teléfono"}


async def test_rejects_third_level_category(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    parent_id = await create_category(client, headers, name="Vivienda")
    child_id = await create_category(client, headers, name="Renta", parent_id=parent_id)

    response = await client.post(
        "/v1/categories",
        json={
            "id": str(uuid.uuid4()),
            "name": "Sub-renta",
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "parent_id": str(child_id),
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "CATEGORY_TOO_DEEP"


async def test_rejects_kind_mismatch_between_parent_and_child(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    parent_id = await create_category(client, headers, kind="expense")

    response = await client.post(
        "/v1/categories",
        json={
            "id": str(uuid.uuid4()),
            "name": "Hijo de otro tipo",
            "kind": "income",
            "category_id": str(await default_income_category(client, headers)),
            "parent_id": str(parent_id),
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "CATEGORY_KIND_MISMATCH"


async def test_merge_reassigns_transactions(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    source_id = await create_category(client, headers, name="Origen")
    target_id = await create_category(client, headers, name="Destino")

    txn_id = uuid.uuid4()
    await client.post(
        "/v1/transactions",
        json={
            "id": str(txn_id),
            "account_id": str(account_id),
            "category_id": str(source_id),
            "kind": "expense",
            "amount_cents": 500,
            "date": "2026-09-04",
        },
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    merge = await client.post(
        f"/v1/categories/{source_id}/merge", json={"into_id": str(target_id)}, headers=headers
    )
    assert merge.status_code == 200

    txn = await client.get(f"/v1/transactions/{txn_id}", headers=headers)
    assert txn.json()["data"]["category_id"] == str(target_id)

    source = await client.get(f"/v1/categories/{source_id}", headers=headers)
    assert source.json()["data"]["is_archived"] is True


async def test_cross_user_cannot_read_category(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, "a@example.com")
    headers_b = await register_and_login(client, "b@example.com")
    category_id = await create_category(client, headers_a)

    response = await client.get(f"/v1/categories/{category_id}", headers=headers_b)
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "CATEGORY_NOT_FOUND"
