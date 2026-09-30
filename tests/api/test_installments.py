import uuid
from typing import Any

from httpx import AsyncClient

from tests.api.helpers import create_account, default_expense_category, register_and_login


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
            "category_id": str(await default_expense_category(client, headers)),
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


async def _pull(client: AsyncClient, headers: dict[str, str], since: int = 0) -> dict[str, Any]:
    response = await client.get(f"/v1/sync/pull?since={since}", headers=headers)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _changes(pull: dict[str, Any], entity_type: str) -> list[dict[str, Any]]:
    changes = pull["changes"]
    assert isinstance(changes, list)
    return [c for c in changes if c["entity_type"] == entity_type]


async def test_create_plan_sends_every_installment_to_the_phone(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=500_000)
    plan_id, installment_ids = await _create_plan(client, headers, account_id, count=3)

    pull = await _pull(client, headers)

    sent = _changes(pull, "installment")
    # Sin estas entradas el calendario local de la app queda vacío: compromiso del
    # mes, pasivo y calendario en cero aunque el plan exista.
    assert len(sent) == 3
    payloads = sorted((c["payload"] for c in sent), key=lambda p: p["number"])
    assert [p["number"] for p in payloads] == [1, 2, 3]
    assert {p["id"] for p in payloads} == {str(i) for i in installment_ids}
    assert all(p["plan_id"] == str(plan_id) for p in payloads)
    assert all(p["status"] == "pending" for p in payloads)
    assert all(p["paid_at"] is None and p["transaction_id"] is None for p in payloads)
    assert sum(p["amount_cents"] for p in payloads) == 1_000_000


async def test_paying_an_installment_syncs_the_transaction_the_balance_and_the_installment(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=2_000_000)
    _plan_id, installment_ids = await _create_plan(client, headers, account_id, count=2)
    before = (await _pull(client, headers))["next_seq"]

    pay = await client.post(
        f"/v1/installments/{installment_ids[0]}/pay",
        json={"id": str(uuid.uuid4()), "date": "2026-09-30"},
        headers={**headers, **_idem()},
    )
    assert pay.status_code == 200, pay.text

    pull = await _pull(client, headers, since=int(before))

    # La transacción llega completa (antes solo traía `id` e `installment_id`, que la
    # app no puede guardar: el pago no aparecía y no movía el saldo).
    (txn,) = _changes(pull, "transaction")
    payload = txn["payload"]
    assert payload["account_id"] == str(account_id)
    assert payload["kind"] == "expense"
    assert payload["amount_cents"] == 500_000
    assert payload["date"] == "2026-09-30"
    assert payload["installment_id"] == str(installment_ids[0])

    (account,) = _changes(pull, "account")
    assert account["payload"]["current_balance_cents"] == 1_500_000

    (paid,) = _changes(pull, "installment")
    assert paid["payload"]["status"] == "paid"
    assert paid["payload"]["transaction_id"] == payload["id"]
