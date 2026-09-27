"""CRUD de ítems de presupuesto, jerarquía padre/hijo (tope advertido, no bloqueado)
y reapertura de períodos cerrados."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from tests.api.helpers import (
    create_account,
    create_category,
    register_and_login,
    register_and_login_with_device,
)


def _current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


async def _create_budget(client: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    budget_id = uuid.uuid4()
    response = await client.post(
        "/v1/budgets",
        json={"id": str(budget_id), "name": "Presupuesto de prueba"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return budget_id


async def _add_item(
    client: AsyncClient,
    headers: dict[str, str],
    budget_id: uuid.UUID,
    category_id: uuid.UUID,
    amount_cents: int,
    *,
    expect: int = 200,
) -> dict[str, Any]:
    item_id = uuid.uuid4()
    response = await client.post(
        f"/v1/budgets/{budget_id}/items",
        json={"id": str(item_id), "category_id": str(category_id), "amount_cents": amount_cents},
        headers=headers,
    )
    assert response.status_code == expect, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


async def _spend(
    client: AsyncClient,
    headers: dict[str, str],
    account_id: uuid.UUID,
    category_id: uuid.UUID,
    amount_cents: int,
) -> None:
    response = await client.post(
        "/v1/transactions",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "category_id": str(category_id),
            "kind": "expense",
            "amount_cents": amount_cents,
            "date": f"{_current_month()}-05",
        },
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 200, response.text


def _by_category(current: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {i["category_id"]: i for i in current["items"]}


# ---------------------------------------------------------------------------
# CRUD


async def test_item_crud_roundtrip(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    category_id = await create_category(client, headers, name="Comida")
    budget_id = await _create_budget(client, headers)

    item = await _add_item(client, headers, budget_id, category_id, 100_000)
    assert item["warning"] is None

    listing = await client.get(f"/v1/budgets/{budget_id}/items", headers=headers)
    assert [i["id"] for i in listing.json()["data"]] == [item["id"]]

    one = await client.get(f"/v1/budgets/{budget_id}/items/{item['id']}", headers=headers)
    assert one.json()["data"]["amount_cents"] == 100_000

    patched = await client.patch(
        f"/v1/budgets/{budget_id}/items/{item['id']}",
        json={"amount_cents": 150_000},
        headers=headers,
    )
    assert patched.json()["data"]["amount_cents"] == 150_000

    deleted = await client.delete(f"/v1/budgets/{budget_id}/items/{item['id']}", headers=headers)
    assert deleted.status_code == 200

    listing = await client.get(f"/v1/budgets/{budget_id}/items", headers=headers)
    assert listing.json()["data"] == []
    gone = await client.get(f"/v1/budgets/{budget_id}/items/{item['id']}", headers=headers)
    assert gone.status_code == 404
    assert gone.json()["data"]["code"] == "BUDGET_ITEM_NOT_FOUND"


async def test_removed_category_can_be_added_again(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    category_id = await create_category(client, headers, name="Comida")
    budget_id = await _create_budget(client, headers)

    first = await _add_item(client, headers, budget_id, category_id, 100_000)
    await client.delete(f"/v1/budgets/{budget_id}/items/{first['id']}", headers=headers)

    second = await _add_item(client, headers, budget_id, category_id, 80_000)
    assert second["id"] != first["id"]


async def test_duplicate_category_is_rejected(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    category_id = await create_category(client, headers, name="Comida")
    budget_id = await _create_budget(client, headers)
    await _add_item(client, headers, budget_id, category_id, 100_000)

    dup = await client.post(
        f"/v1/budgets/{budget_id}/items",
        json={"id": str(uuid.uuid4()), "category_id": str(category_id), "amount_cents": 5},
        headers=headers,
    )
    assert dup.status_code == 409
    assert dup.json()["data"]["code"] == "BUDGET_ITEM_CATEGORY_TAKEN"


async def test_patch_can_change_category(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    food = await create_category(client, headers, name="Comida")
    fun = await create_category(client, headers, name="Ocio")
    transport = await create_category(client, headers, name="Transporte")
    income = await create_category(client, headers, name="Sueldo", kind="income")
    budget_id = await _create_budget(client, headers)
    item = await _add_item(client, headers, budget_id, food, 100_000)
    await _add_item(client, headers, budget_id, fun, 50_000)
    url = f"/v1/budgets/{budget_id}/items/{item['id']}"

    moved = await client.patch(url, json={"category_id": str(transport)}, headers=headers)
    assert moved.status_code == 200
    assert moved.json()["data"]["category_id"] == str(transport)

    taken = await client.patch(url, json={"category_id": str(fun)}, headers=headers)
    assert taken.status_code == 409
    assert taken.json()["data"]["code"] == "BUDGET_ITEM_CATEGORY_TAKEN"

    wrong_kind = await client.patch(url, json={"category_id": str(income)}, headers=headers)
    assert wrong_kind.status_code == 422
    assert wrong_kind.json()["data"]["code"] == "BUDGET_REQUIRES_EXPENSE_CATEGORY"

    null_category = await client.patch(url, json={"category_id": None}, headers=headers)
    assert null_category.status_code == 422


async def test_cross_user_cannot_touch_items(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, "a@example.com")
    headers_b = await register_and_login(client, "b@example.com")
    category_a = await create_category(client, headers_a, name="Comida")
    budget_a = await _create_budget(client, headers_a)
    item = await _add_item(client, headers_a, budget_a, category_a, 100_000)
    item_url = f"/v1/budgets/{budget_a}/items/{item['id']}"

    for response in (
        await client.get(f"/v1/budgets/{budget_a}/items", headers=headers_b),
        await client.get(item_url, headers=headers_b),
        await client.patch(item_url, json={"amount_cents": 1}, headers=headers_b),
        await client.delete(item_url, headers=headers_b),
        await client.delete(
            f"/v1/budgets/{budget_a}/periods/{_current_month()}", headers=headers_b
        ),
    ):
        assert response.status_code == 404

    # No se puede colgar una categoría de otro usuario en el presupuesto propio.
    budget_b = await _create_budget(client, headers_b)
    stolen = await client.post(
        f"/v1/budgets/{budget_b}/items",
        json={"id": str(uuid.uuid4()), "category_id": str(category_a), "amount_cents": 1},
        headers=headers_b,
    )
    assert stolen.status_code == 404
    assert stolen.json()["data"]["code"] == "CATEGORY_NOT_FOUND"


# ---------------------------------------------------------------------------
# Jerarquía padre/hijo


async def test_children_over_parent_cap_warn_but_are_created(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    food = await create_category(client, headers, name="Comida")
    restaurants = await create_category(client, headers, name="Restaurantes", parent_id=food)
    groceries = await create_category(client, headers, name="Súper", parent_id=food)
    budget_id = await _create_budget(client, headers)

    parent = await _add_item(client, headers, budget_id, food, 200_000)
    assert parent["warning"] is None
    fits = await _add_item(client, headers, budget_id, restaurants, 80_000)
    assert fits["warning"] is None

    over = await _add_item(client, headers, budget_id, groceries, 150_000)
    warning = over["warning"]
    assert warning["code"] == "CHILDREN_EXCEED_PARENT"
    assert warning["parent_item_id"] == parent["id"]
    assert warning["parent_category_id"] == str(food)
    assert warning["parent_cents"] == 200_000
    assert warning["children_cents"] == 230_000
    assert warning["excess_cents"] == 30_000

    # Advertir, no bloquear: el ítem quedó creado.
    listing = await client.get(f"/v1/budgets/{budget_id}/items", headers=headers)
    assert len(listing.json()["data"]) == 3


async def test_lowering_parent_cap_warns(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    food = await create_category(client, headers, name="Comida")
    restaurants = await create_category(client, headers, name="Restaurantes", parent_id=food)
    budget_id = await _create_budget(client, headers)
    parent = await _add_item(client, headers, budget_id, food, 200_000)
    await _add_item(client, headers, budget_id, restaurants, 150_000)

    response = await client.patch(
        f"/v1/budgets/{budget_id}/items/{parent['id']}",
        json={"amount_cents": 100_000},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["amount_cents"] == 100_000
    assert response.json()["data"]["warning"]["excess_cents"] == 50_000


async def test_current_rolls_child_spend_into_parent_and_counts_only_roots(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    food = await create_category(client, headers, name="Comida")
    restaurants = await create_category(client, headers, name="Restaurantes", parent_id=food)
    groceries = await create_category(client, headers, name="Súper", parent_id=food)
    budget_id = await _create_budget(client, headers)
    await _add_item(client, headers, budget_id, food, 200_000)
    await _add_item(client, headers, budget_id, restaurants, 120_000)
    await _add_item(client, headers, budget_id, groceries, 100_000)  # hijos: 220k > 200k

    await _spend(client, headers, account_id, food, 10_000)
    await _spend(client, headers, account_id, restaurants, 30_000)
    await _spend(client, headers, account_id, groceries, 5_000)

    response = await client.get("/v1/budgets/current", headers=headers)
    data = response.json()["data"]
    items = _by_category(data)

    assert items[str(food)]["spent_cents"] == 45_000  # 10k propio + 30k + 5k hijos
    assert items[str(restaurants)]["spent_cents"] == 30_000
    assert items[str(groceries)]["spent_cents"] == 5_000

    assert items[str(food)]["parent_category_id"] is None
    assert items[str(restaurants)]["parent_category_id"] == str(food)
    assert items[str(food)]["children_budgeted_cents"] == 220_000
    assert items[str(food)]["children_excess_cents"] == 20_000

    assert data["total_budgeted_cents"] == 200_000  # solo el padre
    assert data["total_spent_cents"] == 45_000  # cada gasto cuenta una sola vez
    assert data["unbudgeted"] == []


async def test_child_spend_under_budgeted_parent_is_not_unbudgeted(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    food = await create_category(client, headers, name="Comida")
    restaurants = await create_category(client, headers, name="Restaurantes", parent_id=food)
    budget_id = await _create_budget(client, headers)
    await _add_item(client, headers, budget_id, food, 200_000)

    await _spend(client, headers, account_id, restaurants, 30_000)

    data = (await client.get("/v1/budgets/current", headers=headers)).json()["data"]
    assert data["unbudgeted"] == []
    assert data["items"][0]["spent_cents"] == 30_000


async def test_child_without_budgeted_parent_is_a_root(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    food = await create_category(client, headers, name="Comida")
    restaurants = await create_category(client, headers, name="Restaurantes", parent_id=food)
    budget_id = await _create_budget(client, headers)
    await _add_item(client, headers, budget_id, restaurants, 80_000)

    data = (await client.get("/v1/budgets/current", headers=headers)).json()["data"]
    assert data["items"][0]["parent_category_id"] is None
    assert data["total_budgeted_cents"] == 80_000


async def test_removing_parent_item_turns_children_into_roots(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    food = await create_category(client, headers, name="Comida")
    restaurants = await create_category(client, headers, name="Restaurantes", parent_id=food)
    budget_id = await _create_budget(client, headers)
    parent = await _add_item(client, headers, budget_id, food, 200_000)
    await _add_item(client, headers, budget_id, restaurants, 80_000)

    await client.delete(f"/v1/budgets/{budget_id}/items/{parent['id']}", headers=headers)

    data = (await client.get("/v1/budgets/current", headers=headers)).json()["data"]
    assert [i["parent_category_id"] for i in data["items"]] == [None]
    assert data["total_budgeted_cents"] == 80_000


async def test_child_expense_pushing_parent_over_threshold_logs_alert(
    client: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    food = await create_category(client, headers, name="Comida")
    restaurants = await create_category(client, headers, name="Restaurantes", parent_id=food)
    budget_id = await _create_budget(client, headers)
    await _add_item(client, headers, budget_id, food, 100_000)

    await _spend(client, headers, account_id, restaurants, 90_000)  # 90% del PADRE

    output = capsys.readouterr().out
    assert "budget_alert" in output
    assert str(food) in output


async def test_frozen_period_keeps_hierarchy(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    food = await create_category(client, headers, name="Comida")
    restaurants = await create_category(client, headers, name="Restaurantes", parent_id=food)
    budget_id = await _create_budget(client, headers)
    await _add_item(client, headers, budget_id, food, 200_000)
    await _add_item(client, headers, budget_id, restaurants, 80_000)
    await _spend(client, headers, account_id, restaurants, 30_000)

    await client.post(
        f"/v1/budgets/{budget_id}/close-period?month={_current_month()}", headers=headers
    )
    data = (
        await client.get(f"/v1/budgets/current?month={_current_month()}", headers=headers)
    ).json()["data"]
    items = _by_category(data)
    assert data["is_closed"] is True
    assert items[str(food)]["spent_cents"] == 30_000
    assert items[str(restaurants)]["parent_category_id"] == str(food)
    assert data["total_budgeted_cents"] == 200_000


# ---------------------------------------------------------------------------
# Reabrir un período cerrado


async def test_reopen_period_recomputes_live(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    food = await create_category(client, headers, name="Comida")
    budget_id = await _create_budget(client, headers)
    await _add_item(client, headers, budget_id, food, 100_000)
    await _spend(client, headers, account_id, food, 60_000)
    month = _current_month()
    await client.post(f"/v1/budgets/{budget_id}/close-period?month={month}", headers=headers)

    reopened = await client.delete(f"/v1/budgets/{budget_id}/periods/{month}", headers=headers)
    assert reopened.status_code == 200

    data = (await client.get(f"/v1/budgets/current?month={month}", headers=headers)).json()["data"]
    assert data["is_closed"] is False
    assert data["items"][0]["spent_cents"] == 60_000

    history = await client.get(f"/v1/budgets/{budget_id}/history", headers=headers)
    assert history.json()["data"]["periods"] == []

    # Y se puede volver a cerrar.
    again = await client.post(
        f"/v1/budgets/{budget_id}/close-period?month={month}", headers=headers
    )
    assert again.status_code == 200


async def test_reopen_period_errors(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    food = await create_category(client, headers, name="Comida")
    budget_id = await _create_budget(client, headers)
    await _add_item(client, headers, budget_id, food, 100_000)

    not_closed = await client.delete(f"/v1/budgets/{budget_id}/periods/2026-01", headers=headers)
    assert not_closed.status_code == 404
    assert not_closed.json()["data"]["code"] == "PERIOD_NOT_FOUND"

    bad_month = await client.delete(f"/v1/budgets/{budget_id}/periods/2026-13", headers=headers)
    assert bad_month.status_code == 422

    for month in ("2026-01", "2026-02"):
        await client.post(f"/v1/budgets/{budget_id}/close-period?month={month}", headers=headers)
    blocked = await client.delete(f"/v1/budgets/{budget_id}/periods/2026-01", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["data"]["code"] == "LATER_PERIOD_CLOSED"

    latest = await client.delete(f"/v1/budgets/{budget_id}/periods/2026-02", headers=headers)
    assert latest.status_code == 200


# ---------------------------------------------------------------------------
# Sync: los ítems viajan por el change log y por /sync/push


async def test_item_changes_reach_sync_pull(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    food = await create_category(client, headers, name="Comida")
    budget_id = await _create_budget(client, headers)
    item = await _add_item(client, headers, budget_id, food, 100_000)
    await client.patch(
        f"/v1/budgets/{budget_id}/items/{item['id']}", json={"amount_cents": 1}, headers=headers
    )
    await client.delete(f"/v1/budgets/{budget_id}/items/{item['id']}", headers=headers)

    pull = await client.get("/v1/sync/pull?since=0", headers=headers)
    ops = [
        c["op"]
        for c in pull.json()["data"]["changes"]
        if c["entity_type"] == "budget_item" and c["entity_id"] == item["id"]
    ]
    assert ops == ["upsert", "upsert", "delete"]


async def test_sync_push_deletes_item_and_conflicts_on_later_update(client: AsyncClient) -> None:
    headers, device_id = await register_and_login_with_device(client)
    food = await create_category(client, headers, name="Comida")
    budget_id = await _create_budget(client, headers)
    item = await _add_item(client, headers, budget_id, food, 100_000)

    def mutation(op: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "client_mutation_id": str(uuid.uuid4()),
            "entity_type": "budget_item",
            "entity_id": item["id"],
            "op": op,
            "payload": payload,
            "client_updated_at": datetime.now(UTC).isoformat(),
        }

    delete = await client.post(
        "/v1/sync/push",
        json={"device_id": str(device_id), "mutations": [mutation("delete", {})]},
        headers=headers,
    )
    assert delete.json()["data"]["conflicts"] == []
    listing = await client.get(f"/v1/budgets/{budget_id}/items", headers=headers)
    assert listing.json()["data"] == []

    update = await client.post(
        "/v1/sync/push",
        json={
            "device_id": str(device_id),
            "mutations": [mutation("upsert", {"budget_id": str(budget_id), "amount_cents": 5})],
        },
        headers=headers,
    )
    conflicts = update.json()["data"]["conflicts"]
    assert [c["reason"] for c in conflicts] == ["DELETED_ON_SERVER"]


# ---------------------------------------------------------------------------
# Archivar / fusionar categorías no dejan ítems huérfanos


async def test_archiving_a_category_removes_its_budget_item(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    food = await create_category(client, headers, name="Comida")
    fun = await create_category(client, headers, name="Ocio")
    budget_id = await _create_budget(client, headers)
    await _add_item(client, headers, budget_id, food, 100_000)
    await _add_item(client, headers, budget_id, fun, 50_000)

    archived = await client.delete(f"/v1/categories/{food}", headers=headers)
    assert archived.status_code == 200

    items = (await client.get(f"/v1/budgets/{budget_id}/items", headers=headers)).json()["data"]
    assert [i["category_id"] for i in items] == [str(fun)]
    current = (await client.get("/v1/budgets/current", headers=headers)).json()["data"]
    assert current["total_budgeted_cents"] == 50_000


async def test_merging_categories_sums_budget_items(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    food = await create_category(client, headers, name="Comida")
    dining = await create_category(client, headers, name="Comer afuera")
    budget_id = await _create_budget(client, headers)
    await _add_item(client, headers, budget_id, food, 100_000)
    await _add_item(client, headers, budget_id, dining, 40_000)

    merged = await client.post(
        f"/v1/categories/{dining}/merge", json={"into_id": str(food)}, headers=headers
    )
    assert merged.status_code == 200, merged.text

    items = (await client.get(f"/v1/budgets/{budget_id}/items", headers=headers)).json()["data"]
    assert [(i["category_id"], i["amount_cents"]) for i in items] == [(str(food), 140_000)]


async def test_merging_moves_item_when_target_has_none(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    food = await create_category(client, headers, name="Comida")
    dining = await create_category(client, headers, name="Comer afuera")
    budget_id = await _create_budget(client, headers)
    item = await _add_item(client, headers, budget_id, dining, 40_000)

    await client.post(
        f"/v1/categories/{dining}/merge", json={"into_id": str(food)}, headers=headers
    )

    items = (await client.get(f"/v1/budgets/{budget_id}/items", headers=headers)).json()["data"]
    assert [(i["id"], i["category_id"]) for i in items] == [(item["id"], str(food))]


async def test_copy_from_previous_writes_items_to_change_log(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    food = await create_category(client, headers, name="Comida")
    budget_id = await _create_budget(client, headers)
    await _add_item(client, headers, budget_id, food, 100_000)
    now = datetime.now(UTC)
    previous = f"{now.year - 1}-12" if now.month == 1 else f"{now.year}-{now.month - 1:02d}"
    await client.post(f"/v1/budgets/{budget_id}/close-period?month={previous}", headers=headers)
    first_pull = await client.get("/v1/sync/pull?since=0", headers=headers)
    since = first_pull.json()["data"]["next_seq"]

    copied = await client.post(f"/v1/budgets/{budget_id}/copy-from-previous", headers=headers)
    assert copied.status_code == 200, copied.text

    pull = await client.get(f"/v1/sync/pull?since={since}", headers=headers)
    kinds = [c["entity_type"] for c in pull.json()["data"]["changes"]]
    assert kinds == ["budget_item"]
