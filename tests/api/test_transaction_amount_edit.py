"""PATCH /transactions/{id} con amount_cents: recalcula el saldo de la cuenta
en la misma escritura, y se bloquea en toda transacción cuyo monto otra fila
asuma que coincide (cuota, liquidación de "me deben", pago de deuda,
transferencia)."""

import uuid
from datetime import UTC, datetime
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


def _current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


async def _create_transaction(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    account_id: uuid.UUID,
    kind: str = "expense",
    amount_cents: int = 10_000,
    category_id: uuid.UUID | None = None,
    currency: str = "GTQ",
    fx_rate: str | None = None,
) -> uuid.UUID:
    if category_id is None and kind == "expense":
        category_id = await default_expense_category(client, headers)
    if category_id is None and kind == "income":
        category_id = await default_income_category(client, headers)
    transaction_id = uuid.uuid4()
    payload: dict[str, object] = {
        "id": str(transaction_id),
        "account_id": str(account_id),
        "kind": kind,
        "amount_cents": amount_cents,
        "currency": currency,
        "date": f"{_current_month()}-05",
    }
    if category_id is not None:
        payload["category_id"] = str(category_id)
    if fx_rate is not None:
        payload["fx_rate"] = fx_rate
    response = await client.post("/v1/transactions", json=payload, headers={**headers, **_idem()})
    assert response.status_code == 200, response.text
    return transaction_id


async def _balance(client: AsyncClient, headers: dict[str, str], account_id: uuid.UUID) -> int:
    response = await client.get(f"/v1/accounts/{account_id}", headers=headers)
    data: int = response.json()["data"]["current_balance_cents"]
    return data


# ---------------------------------------------------------------------------
# Caso normal: recalcula el saldo


async def test_editing_expense_amount_adjusts_balance(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    txn_id = await _create_transaction(client, headers, account_id=account_id, amount_cents=30_000)
    assert await _balance(client, headers, account_id) == 970_000

    response = await client.patch(
        f"/v1/transactions/{txn_id}", json={"amount_cents": 50_000}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["amount_cents"] == 50_000
    assert await _balance(client, headers, account_id) == 950_000


async def test_editing_income_amount_adjusts_balance(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=100_000)
    txn_id = await _create_transaction(
        client, headers, account_id=account_id, kind="income", amount_cents=20_000
    )
    assert await _balance(client, headers, account_id) == 120_000

    response = await client.patch(
        f"/v1/transactions/{txn_id}", json={"amount_cents": 5_000}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert await _balance(client, headers, account_id) == 105_000


async def test_editing_expense_on_credit_card_increases_debt(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(
        client, headers, account_type="credit_card", initial_balance_cents=10_000
    )
    txn_id = await _create_transaction(client, headers, account_id=account_id, amount_cents=1_000)
    assert await _balance(client, headers, account_id) == 11_000  # deuda sube

    await client.patch(f"/v1/transactions/{txn_id}", json={"amount_cents": 4_000}, headers=headers)
    assert await _balance(client, headers, account_id) == 14_000


async def test_editing_to_the_same_amount_is_a_noop(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=100_000)
    txn_id = await _create_transaction(client, headers, account_id=account_id, amount_cents=10_000)
    balance_before = await _balance(client, headers, account_id)

    response = await client.patch(
        f"/v1/transactions/{txn_id}", json={"amount_cents": 10_000}, headers=headers
    )
    assert response.status_code == 200
    assert await _balance(client, headers, account_id) == balance_before


async def test_amount_cents_rejects_explicit_null_and_non_positive(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=100_000)
    txn_id = await _create_transaction(client, headers, account_id=account_id, amount_cents=10_000)

    null_amount = await client.patch(
        f"/v1/transactions/{txn_id}", json={"amount_cents": None}, headers=headers
    )
    assert null_amount.status_code == 422

    zero_amount = await client.patch(
        f"/v1/transactions/{txn_id}", json={"amount_cents": 0}, headers=headers
    )
    assert zero_amount.status_code == 422


async def test_editing_foreign_currency_amount_keeps_the_frozen_fx_rate(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(
        client, headers, initial_balance_cents=1_000_000, currency="USD"
    )
    txn_id = await _create_transaction(
        client,
        headers,
        account_id=account_id,
        amount_cents=1_000,  # $10.00
        currency="USD",
        fx_rate="7.80",
    )

    response = await client.patch(
        f"/v1/transactions/{txn_id}",
        json={"amount_cents": 2_000},
        headers=headers,  # $20.00
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["amount_cents"] == 2_000
    assert Decimal(data["fx_rate"]) == Decimal("7.80")
    assert data["base_amount_cents"] == 15_600  # 2000 * 7.80, la MISMA tasa


# ---------------------------------------------------------------------------
# Bloqueado: el monto de estas transacciones lo asume otra fila


async def test_transfer_leg_amount_cannot_be_edited(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_a = await create_account(client, headers, initial_balance_cents=1_000_000)
    account_b = await create_account(client, headers, initial_balance_cents=0)
    out_id, in_id = uuid.uuid4(), uuid.uuid4()
    response = await client.post(
        "/v1/transactions/transfer",
        json={
            "out_transaction_id": str(out_id),
            "in_transaction_id": str(in_id),
            "from_account_id": str(account_a),
            "to_account_id": str(account_b),
            "amount_cents": 50_000,
            "date": f"{_current_month()}-05",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text

    edit = await client.patch(
        f"/v1/transactions/{out_id}", json={"amount_cents": 60_000}, headers=headers
    )
    assert edit.status_code == 422
    assert edit.json()["data"]["code"] == "TRANSFER_AMOUNT_EDIT_UNSUPPORTED"


async def test_installment_transaction_amount_is_locked(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    plan_id = uuid.uuid4()
    installment_id = uuid.uuid4()
    response = await client.post(
        "/v1/installment-plans",
        json={
            "id": str(plan_id),
            "account_id": str(account_id),
            "category_id": str(await default_expense_category(client, headers)),
            "description": "Refri",
            "total_amount_cents": 120_000,
            "installments_count": 1,
            "first_payment_date": f"{_current_month()}-05",
            "installment_ids": [str(installment_id)],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text

    pay = await client.post(
        f"/v1/installments/{installment_id}/pay",
        json={"id": str(uuid.uuid4()), "date": f"{_current_month()}-05"},
        headers={**headers, **_idem()},
    )
    assert pay.status_code == 200, pay.text
    txn_id = pay.json()["data"]["transaction_id"]
    assert txn_id is not None

    edit = await client.patch(
        f"/v1/transactions/{txn_id}", json={"amount_cents": 999}, headers=headers
    )
    assert edit.status_code == 422
    assert edit.json()["data"]["code"] == "INSTALLMENT_AMOUNT_LOCKED"


async def test_receivable_settlement_amount_is_locked(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    expense_id = await _create_transaction(
        client, headers, account_id=account_id, amount_cents=200_000
    )
    receivable_id = uuid.uuid4()
    await client.post(
        "/v1/receivables",
        json={
            "id": str(receivable_id),
            "transaction_id": str(expense_id),
            "counterparty": "Ana",
            "amount_cents": 100_000,
        },
        headers=headers,
    )
    settle = await client.post(
        f"/v1/receivables/{receivable_id}/settle",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "date": f"{_current_month()}-10",
        },
        headers={**headers, **_idem()},
    )
    assert settle.status_code == 200, settle.text
    txn_id = settle.json()["data"]["settlement_transaction_id"]
    assert txn_id is not None

    edit = await client.patch(
        f"/v1/transactions/{txn_id}", json={"amount_cents": 1}, headers=headers
    )
    assert edit.status_code == 422
    assert edit.json()["data"]["code"] == "RECEIVABLE_AMOUNT_LOCKED"


async def test_debt_payment_transaction_amount_is_locked(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    debt_id = uuid.uuid4()
    await client.post(
        "/v1/debts",
        json={
            "id": str(debt_id),
            "name": "Préstamo",
            "principal_cents": 500_000,
            "monthly_interest_rate": "0",
            "start_date": f"{_current_month()}-01",
        },
        headers=headers,
    )
    payment = await client.post(
        f"/v1/debts/{debt_id}/payments",
        json={
            "id": str(uuid.uuid4()),
            "from_account_id": str(account_id),
            "date": f"{_current_month()}-05",
            "total_cents": 50_000,
            "fees_cents": 0,
            "principal_cents": 50_000,
            "interest_cents": 0,
            "interest_transaction_id": str(uuid.uuid4()),
            "principal_transaction_id": str(uuid.uuid4()),
            "principal_transfer_out_id": str(uuid.uuid4()),
            "principal_transfer_in_id": str(uuid.uuid4()),
        },
        headers={**headers, **_idem()},
    )
    assert payment.status_code == 200, payment.text
    txn_id = payment.json()["data"]["transaction_id"]
    assert txn_id is not None

    edit = await client.patch(
        f"/v1/transactions/{txn_id}", json={"amount_cents": 1}, headers=headers
    )
    assert edit.status_code == 422
    assert edit.json()["data"]["code"] == "DEBT_PAYMENT_AMOUNT_LOCKED"


# ---------------------------------------------------------------------------
# Sí editables: no hay otra fila cuyo monto dependa del suyo


async def test_refund_amount_can_be_edited(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    category_id = await create_category(client, headers, name="Comida")
    expense_id = await _create_transaction(
        client, headers, account_id=account_id, amount_cents=50_000, category_id=category_id
    )
    refund = await client.post(
        f"/v1/transactions/{expense_id}/refund",
        json={"id": str(uuid.uuid4()), "date": f"{_current_month()}-06"},
        headers={**headers, **_idem()},
    )
    assert refund.status_code == 200, refund.text
    refund_id = refund.json()["data"]["id"]
    balance_after_refund = await _balance(client, headers, account_id)

    edit = await client.patch(
        f"/v1/transactions/{refund_id}", json={"amount_cents": 10_000}, headers=headers
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["data"]["amount_cents"] == 10_000
    # Bajó de 50_000 a 10_000 de reembolso: el saldo cae en la diferencia (40_000).
    assert await _balance(client, headers, account_id) == balance_after_refund - 40_000


async def test_recurring_confirmation_amount_can_be_edited(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    rule_id = uuid.uuid4()
    await client.post(
        "/v1/recurring-rules",
        json={
            "id": str(rule_id),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "name": "Netflix",
            "amount_cents": 5_000,
            "frequency": "monthly",
            "next_due_date": f"{_current_month()}-05",
            "auto_create": False,
        },
        headers=headers,
    )
    confirm = await client.post(
        f"/v1/recurring-rules/{rule_id}/confirm",
        json={"id": str(uuid.uuid4()), "date": f"{_current_month()}-05"},
        headers={**headers, **_idem()},
    )
    assert confirm.status_code == 200, confirm.text
    txn_id = confirm.json()["data"]["id"]

    edit = await client.patch(
        f"/v1/transactions/{txn_id}", json={"amount_cents": 6_500}, headers=headers
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["data"]["amount_cents"] == 6_500


# ---------------------------------------------------------------------------
# Acceso cruzado


async def test_cross_user_cannot_edit_amount(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, "amount-a@example.com")
    headers_b = await register_and_login(client, "amount-b@example.com")
    account_id = await create_account(client, headers_a, initial_balance_cents=100_000)
    txn_id = await _create_transaction(
        client, headers_a, account_id=account_id, amount_cents=10_000
    )

    response = await client.patch(
        f"/v1/transactions/{txn_id}", json={"amount_cents": 1}, headers=headers_b
    )
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "TRANSACTION_NOT_FOUND"
    assert await _balance(client, headers_a, account_id) == 90_000  # sin cambios
