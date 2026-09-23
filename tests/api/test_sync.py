import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from tests.api.helpers import create_account, create_category, register_and_login_with_device


async def test_pull_returns_changes_since_cursor(client: AsyncClient) -> None:
    headers, _device = await register_and_login_with_device(client)
    account_id = await create_account(client, headers)

    pull = await client.get("/v1/sync/pull?since=0", headers=headers)
    assert pull.status_code == 200
    changes = pull.json()["data"]["changes"]
    assert any(c["entity_type"] == "account" and c["entity_id"] == str(account_id) for c in changes)
    assert pull.json()["data"]["has_more"] is False


async def test_pull_since_latest_returns_nothing_new(client: AsyncClient) -> None:
    headers, _device = await register_and_login_with_device(client)
    await create_account(client, headers)

    status = await client.get("/v1/sync/status", headers=headers)
    latest = status.json()["data"]["server_seq"]

    pull = await client.get(f"/v1/sync/pull?since={latest}", headers=headers)
    assert pull.json()["data"]["changes"] == []


async def test_push_creates_account_offline(client: AsyncClient) -> None:
    headers, device_id = await register_and_login_with_device(client)
    account_id = uuid.uuid4()

    response = await client.post(
        "/v1/sync/push",
        json={
            "device_id": str(device_id),
            "mutations": [
                {
                    "client_mutation_id": str(uuid.uuid4()),
                    "entity_type": "account",
                    "entity_id": str(account_id),
                    "op": "upsert",
                    "payload": {
                        "id": str(account_id),
                        "name": "Cuenta offline",
                        "type": "cash",
                        "initial_balance_cents": 500,
                    },
                    "client_updated_at": datetime.now(UTC).isoformat(),
                }
            ],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["conflicts"] == []

    account = await client.get(f"/v1/accounts/{account_id}", headers=headers)
    assert account.status_code == 200
    assert account.json()["data"]["name"] == "Cuenta offline"


async def test_push_with_unknown_device_is_rejected(client: AsyncClient) -> None:
    headers, _device = await register_and_login_with_device(client)

    response = await client.post(
        "/v1/sync/push",
        json={
            "device_id": str(uuid.uuid4()),
            "mutations": [],
        },
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "DEVICE_NOT_FOUND"


async def test_resending_same_mutation_is_noop(client: AsyncClient) -> None:
    headers, device_id = await register_and_login_with_device(client)
    category_id = uuid.uuid4()
    mutation_id = str(uuid.uuid4())
    body = {
        "device_id": str(device_id),
        "mutations": [
            {
                "client_mutation_id": mutation_id,
                "entity_type": "category",
                "entity_id": str(category_id),
                "op": "upsert",
                "payload": {"id": str(category_id), "name": "Comida", "kind": "expense"},
                "client_updated_at": datetime.now(UTC).isoformat(),
            }
        ],
    }

    first = await client.post("/v1/sync/push", json=body, headers=headers)
    second = await client.post("/v1/sync/push", json=body, headers=headers)
    assert first.json()["data"]["applied"] == second.json()["data"]["applied"]

    listing = await client.get("/v1/categories", headers=headers)
    assert len(listing.json()["data"]) == 1  # no se duplicó


async def test_two_devices_edit_same_transaction_last_write_wins(client: AsyncClient) -> None:
    """Escenario obligatorio de PLAN-backend §11: dos dispositivos offline
    editan la misma transacción; converge y el saldo no queda mal."""
    headers, device_a = await register_and_login_with_device(client, "user@example.com")
    _headers_b, device_b = await register_and_login_with_device(
        client, "user@example.com", uuid.uuid4()
    )
    account_id = await create_account(client, headers, initial_balance_cents=10_000)
    category_a = await create_category(client, headers, name="Categoría A")
    category_b = await create_category(client, headers, name="Categoría B")

    txn_id = uuid.uuid4()
    await client.post(
        "/v1/transactions",
        json={
            "id": str(txn_id),
            "account_id": str(account_id),
            "kind": "expense",
            "amount_cents": 500,
            "date": "2026-09-04",
            "description": "original",
        },
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    now = datetime.now(UTC)
    older_edit = {
        "device_id": str(device_a),
        "mutations": [
            {
                "client_mutation_id": str(uuid.uuid4()),
                "entity_type": "transaction",
                "entity_id": str(txn_id),
                "op": "upsert",
                "payload": {"category_id": str(category_a), "description": "editado por A"},
                "client_updated_at": (now - timedelta(minutes=10)).isoformat(),
            }
        ],
    }
    newer_edit = {
        "device_id": str(device_b),
        "mutations": [
            {
                "client_mutation_id": str(uuid.uuid4()),
                "entity_type": "transaction",
                "entity_id": str(txn_id),
                "op": "upsert",
                "payload": {"category_id": str(category_b), "description": "editado por B"},
                "client_updated_at": now.isoformat(),
            }
        ],
    }

    # El edit más nuevo llega primero (orden de red no garantizado).
    newer_response = await client.post("/v1/sync/push", json=newer_edit, headers=headers)
    assert newer_response.json()["data"]["conflicts"] == []

    older_response = await client.post("/v1/sync/push", json=older_edit, headers=headers)
    assert len(older_response.json()["data"]["conflicts"]) == 1
    assert older_response.json()["data"]["conflicts"][0]["reason"] == "STALE_UPDATE"

    txn = await client.get(f"/v1/transactions/{txn_id}", headers=headers)
    # Gana B (el más nuevo); el saldo de la cuenta no se tocó por ninguno de los dos edits.
    assert txn.json()["data"]["description"] == "editado por B"
    assert txn.json()["data"]["category_id"] == str(category_b)

    account = await client.get(f"/v1/accounts/{account_id}", headers=headers)
    assert account.json()["data"]["current_balance_cents"] == 9_500


async def test_delete_beats_update(client: AsyncClient) -> None:
    headers, device_id = await register_and_login_with_device(client)
    account_id = await create_account(client, headers)

    delete_mutation = {
        "device_id": str(device_id),
        "mutations": [
            {
                "client_mutation_id": str(uuid.uuid4()),
                "entity_type": "account",
                "entity_id": str(account_id),
                "op": "delete",
                "payload": {},
                "client_updated_at": datetime.now(UTC).isoformat(),
            }
        ],
    }
    await client.post("/v1/sync/push", json=delete_mutation, headers=headers)

    update_mutation = {
        "device_id": str(device_id),
        "mutations": [
            {
                "client_mutation_id": str(uuid.uuid4()),
                "entity_type": "account",
                "entity_id": str(account_id),
                "op": "upsert",
                "payload": {"name": "Intento de revivir"},
                "client_updated_at": datetime.now(UTC).isoformat(),
            }
        ],
    }
    response = await client.post("/v1/sync/push", json=update_mutation, headers=headers)
    assert response.json()["data"]["conflicts"][0]["reason"] == "DELETED_ON_SERVER"


async def test_transfer_creation_not_supported_via_push(client: AsyncClient) -> None:
    headers, device_id = await register_and_login_with_device(client)
    await create_account(client, headers)

    mutation = {
        "device_id": str(device_id),
        "mutations": [
            {
                "client_mutation_id": str(uuid.uuid4()),
                "entity_type": "transaction",
                "entity_id": str(uuid.uuid4()),
                "op": "upsert",
                "payload": {"kind": "transfer"},
                "client_updated_at": datetime.now(UTC).isoformat(),
            }
        ],
    }
    response = await client.post("/v1/sync/push", json=mutation, headers=headers)
    assert (
        response.json()["data"]["conflicts"][0]["reason"] == "TRANSFER_NOT_SUPPORTED_VIA_SYNC_PUSH"
    )


async def test_push_creates_budget_and_item_offline(client: AsyncClient) -> None:
    headers, device_id = await register_and_login_with_device(client)
    category_id = await create_category(client, headers, name="Alimentación")
    budget_id = str(uuid.uuid4())
    item_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    response = await client.post(
        "/v1/sync/push",
        json={
            "device_id": str(device_id),
            "mutations": [
                {
                    "client_mutation_id": str(uuid.uuid4()),
                    "entity_type": "budget",
                    "entity_id": budget_id,
                    "op": "upsert",
                    "payload": {"id": budget_id, "name": "Septiembre"},
                    "client_updated_at": now,
                },
                {
                    "client_mutation_id": str(uuid.uuid4()),
                    "entity_type": "budget_item",
                    "entity_id": item_id,
                    "op": "upsert",
                    "payload": {
                        "id": item_id,
                        "budget_id": budget_id,
                        "category_id": str(category_id),
                        "amount_cents": 250_000,
                    },
                    "client_updated_at": now,
                },
            ],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["conflicts"] == []

    current = await client.get("/v1/budgets/current", headers=headers)
    assert current.status_code == 200
    assert current.json()["data"]["items"][0]["budgeted_cents"] == 250_000


async def test_push_with_invalid_mutation_does_not_break_the_batch(client: AsyncClient) -> None:
    headers, device_id = await register_and_login_with_device(client)
    account_id = await create_account(client, headers, name="Original")
    other_id = uuid.uuid4()
    bad_id, good_id = str(uuid.uuid4()), str(uuid.uuid4())
    later = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()

    response = await client.post(
        "/v1/sync/push",
        json={
            "device_id": str(device_id),
            "mutations": [
                {
                    "client_mutation_id": bad_id,
                    "entity_type": "account",
                    "entity_id": str(account_id),
                    "op": "upsert",
                    "payload": {"name": None},
                    "client_updated_at": later,
                },
                {
                    "client_mutation_id": good_id,
                    "entity_type": "account",
                    "entity_id": str(other_id),
                    "op": "upsert",
                    "payload": {"id": str(other_id), "name": "Buena", "type": "cash"},
                    "client_updated_at": later,
                },
            ],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["applied"] == [good_id]
    assert [(c["client_mutation_id"], c["reason"]) for c in data["conflicts"]] == [
        (bad_id, "VALIDATION_ERROR")
    ]

    unchanged = await client.get(f"/v1/accounts/{account_id}", headers=headers)
    assert unchanged.json()["data"]["name"] == "Original"
