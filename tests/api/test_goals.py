import uuid

from httpx import AsyncClient

from tests.api.helpers import create_account, register_and_login


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def test_contribute_without_linked_account_just_bumps_progress(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    goal_id = uuid.uuid4()
    await client.post(
        "/v1/goals",
        json={"id": str(goal_id), "name": "Fondo de emergencia", "target_amount_cents": 500_000},
        headers=headers,
    )

    response = await client.post(
        f"/v1/goals/{goal_id}/contribute",
        json={"id": str(uuid.uuid4()), "amount_cents": 100_000, "date": "2026-09-04"},
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["transaction_id"] is None

    goal = await client.get(f"/v1/goals/{goal_id}", headers=headers)
    assert goal.json()["data"]["current_amount_cents"] == 100_000
    assert goal.json()["data"]["status"] == "active"


async def test_contribute_with_linked_account_transfers_real_money(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    checking = await create_account(client, headers, initial_balance_cents=500_000)
    savings = await create_account(client, headers, initial_balance_cents=0)

    goal_id = uuid.uuid4()
    await client.post(
        "/v1/goals",
        json={
            "id": str(goal_id),
            "name": "Viaje",
            "target_amount_cents": 100_000,
            "linked_account_id": str(savings),
        },
        headers=headers,
    )

    response = await client.post(
        f"/v1/goals/{goal_id}/contribute",
        json={
            "id": str(uuid.uuid4()),
            "amount_cents": 100_000,
            "date": "2026-09-04",
            "from_account_id": str(checking),
            "transfer_out_id": str(uuid.uuid4()),
            "transfer_in_id": str(uuid.uuid4()),
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["transaction_id"] is not None

    goal = await client.get(f"/v1/goals/{goal_id}", headers=headers)
    assert goal.json()["data"]["current_amount_cents"] == 100_000
    assert goal.json()["data"]["status"] == "completed"

    checking_acc = await client.get(f"/v1/accounts/{checking}", headers=headers)
    savings_acc = await client.get(f"/v1/accounts/{savings}", headers=headers)
    assert checking_acc.json()["data"]["current_balance_cents"] == 400_000
    assert savings_acc.json()["data"]["current_balance_cents"] == 100_000


async def test_contribute_with_linked_account_requires_transfer_ids(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    checking = await create_account(client, headers, initial_balance_cents=500_000)
    savings = await create_account(client, headers)

    goal_id = uuid.uuid4()
    await client.post(
        "/v1/goals",
        json={
            "id": str(goal_id),
            "name": "Viaje",
            "target_amount_cents": 100_000,
            "linked_account_id": str(savings),
        },
        headers=headers,
    )

    response = await client.post(
        f"/v1/goals/{goal_id}/contribute",
        json={
            "id": str(uuid.uuid4()),
            "amount_cents": 50_000,
            "date": "2026-09-04",
            "from_account_id": str(checking),
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "GOAL_TRANSFER_IDS_REQUIRED"


async def test_cross_user_cannot_read_goal(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, "a@example.com")
    headers_b = await register_and_login(client, "b@example.com")
    goal_id = uuid.uuid4()
    await client.post(
        "/v1/goals",
        json={"id": str(goal_id), "name": "Meta de A", "target_amount_cents": 1000},
        headers=headers_a,
    )
    response = await client.get(f"/v1/goals/{goal_id}", headers=headers_b)
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "GOAL_NOT_FOUND"
