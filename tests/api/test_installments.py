import uuid

from httpx import AsyncClient

from tests.api.helpers import create_account, register_and_login


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def _create_plan(
    client: AsyncClient, headers: dict[str, str], account_id: uuid.UUID, count: int = 12
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    plan_id = uuid.uuid4()
    installment_ids = [uuid.uuid4() for _ in range(count)]
    response = await client.post(
        "/v1/installment-plans",
        json={
            "id": str(plan_id),
            "account_id": str(account_id),
            "description": "Celular",
            "total_amount_cents": 1_000_000,
            "installments_count": count,
            "first_payment_date": "2026-09-15",
            "installment_ids": [str(i) for i in installment_ids],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return plan_id, installment_ids


async def test_create_plan_does_not_touch_balance_case_06(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=500_000)

    await _create_plan(client, headers, account_id)

    account = await client.get(f"/v1/accounts/{account_id}", headers=headers)
    # Comprar en 12 cuotas no es un gasto del mes por el total (caso 6).
    assert account.json()["data"]["current_balance_cents"] == 500_000


async def test_schedule_sums_to_total_and_never_loses_a_cent(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    plan_id, _ = await _create_plan(client, headers, account_id)

    schedule = await client.get(f"/v1/installment-plans/{plan_id}/schedule", headers=headers)
    rows = schedule.json()["data"]["schedule"]
    assert len(rows) == 12
    assert sum(r["amount_cents"] for r in rows) == 1_000_000
    assert all(r["status"] == "pending" for r in rows)


async def test_pay_installment_creates_expense_and_updates_balance(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=500_000)
    plan_id, installment_ids = await _create_plan(client, headers, account_id)

    response = await client.post(
        f"/v1/installments/{installment_ids[0]}/pay",
        json={"id": str(uuid.uuid4()), "date": "2026-09-15"},
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "paid"

    account = await client.get(f"/v1/accounts/{account_id}", headers=headers)
    assert account.json()["data"]["current_balance_cents"] == 500_000 - 83_333


async def test_pay_installment_twice_is_conflict(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    _plan_id, installment_ids = await _create_plan(client, headers, account_id)

    await client.post(
        f"/v1/installments/{installment_ids[0]}/pay",
        json={"id": str(uuid.uuid4())},
        headers={**headers, **_idem()},
    )
    response = await client.post(
        f"/v1/installments/{installment_ids[0]}/pay",
        json={"id": str(uuid.uuid4())},
        headers={**headers, **_idem()},
    )
    assert response.status_code == 409
    assert response.json()["data"]["code"] == "INSTALLMENT_NOT_PENDING"


async def test_liability_totals_pending_installments(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    plan_id, installment_ids = await _create_plan(client, headers, account_id, count=3)

    schedule = await client.get(f"/v1/installment-plans/{plan_id}/schedule", headers=headers)
    total_amount = sum(r["amount_cents"] for r in schedule.json()["data"]["schedule"])

    liability = await client.get("/v1/installments/liability", headers=headers)
    assert liability.json()["data"]["total_pending_cents"] == total_amount

    await client.post(
        f"/v1/installments/{installment_ids[0]}/pay",
        json={"id": str(uuid.uuid4())},
        headers={**headers, **_idem()},
    )
    liability_after = await client.get("/v1/installments/liability", headers=headers)
    paid_amount = next(
        r["amount_cents"] for r in schedule.json()["data"]["schedule"] if r["number"] == 1
    )
    assert liability_after.json()["data"]["total_pending_cents"] == total_amount - paid_amount


async def test_delete_plan_removes_pending_but_keeps_paid(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=500_000)
    plan_id, installment_ids = await _create_plan(client, headers, account_id, count=3)

    await client.post(
        f"/v1/installments/{installment_ids[0]}/pay",
        json={"id": str(uuid.uuid4())},
        headers={**headers, **_idem()},
    )
    delete_resp = await client.delete(f"/v1/installment-plans/{plan_id}", headers=headers)
    assert delete_resp.status_code == 200

    # El plan queda archivado (deleted_at); ya no se lista, pero existía.
    plans = await client.get("/v1/installment-plans", headers=headers)
    assert plans.json()["data"] == []


async def test_cross_user_cannot_read_plan(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, "a@example.com")
    headers_b = await register_and_login(client, "b@example.com")
    account_id = await create_account(client, headers_a)
    plan_id, _ = await _create_plan(client, headers_a, account_id)

    response = await client.get(f"/v1/installment-plans/{plan_id}", headers=headers_b)
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "INSTALLMENT_PLAN_NOT_FOUND"
