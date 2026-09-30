"""Un gasto o un ingreso no pueden existir sin categoría (`domain/category_rule.py`).

Vale para todo lo que termina siendo un gasto o un ingreso: la transacción, la regla
recurrente, el plan de cuotas (siempre gasto) y la plantilla, y también cuando llega
por `/sync/push`. Las transferencias entre cuentas propias no llevan categoría.
"""

import uuid
from datetime import UTC, datetime

from httpx import AsyncClient, Response

from tests.api.helpers import (
    create_account,
    create_category,
    default_expense_category,
    register_and_login,
    register_and_login_with_device,
)


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def _transaction(
    account_id: uuid.UUID, kind: str = "expense", **extra: object
) -> dict[str, object]:
    return {
        "id": str(uuid.uuid4()),
        "account_id": str(account_id),
        "kind": kind,
        "amount_cents": 5_000,
        "date": "2026-09-05",
        **extra,
    }


def _plan(account_id: uuid.UUID, **extra: object) -> dict[str, object]:
    return {
        "id": str(uuid.uuid4()),
        "account_id": str(account_id),
        "description": "Refri",
        "total_amount_cents": 120_000,
        "installments_count": 1,
        "first_payment_date": "2026-09-15",
        "installment_ids": [str(uuid.uuid4())],
        **extra,
    }


def _rule(account_id: uuid.UUID, kind: str = "expense", **extra: object) -> dict[str, object]:
    return {
        "id": str(uuid.uuid4()),
        "account_id": str(account_id),
        "kind": kind,
        "name": "Netflix",
        "amount_cents": 8_900,
        "frequency": "monthly",
        "next_due_date": "2026-09-10",
        **extra,
    }


def _is_category_required(response: Response) -> bool:
    data = response.json()["data"]
    return bool(
        response.status_code == 422
        and data["code"] == "CATEGORY_REQUIRED"
        and data["field"] == "category_id"
    )


# ---------- transacciones


async def test_expense_without_category_is_rejected_and_does_not_touch_the_balance(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=10_000)

    response = await client.post(
        "/v1/transactions", json=_transaction(account_id), headers={**headers, **_idem()}
    )

    assert _is_category_required(response), response.text
    account = await client.get(f"/v1/accounts/{account_id}", headers=headers)
    assert account.json()["data"]["current_balance_cents"] == 10_000


async def test_expense_with_category_is_accepted(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    category_id = await default_expense_category(client, headers)

    response = await client.post(
        "/v1/transactions",
        json=_transaction(account_id, category_id=str(category_id)),
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text


async def test_income_without_category_is_rejected(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)

    response = await client.post(
        "/v1/transactions",
        json=_transaction(account_id, kind="income"),
        headers={**headers, **_idem()},
    )
    assert _is_category_required(response), response.text


async def test_income_with_category_is_accepted(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    income_category = await create_category(client, headers, kind="income", name="Salario")

    response = await client.post(
        "/v1/transactions",
        json=_transaction(account_id, kind="income", category_id=str(income_category)),
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text


async def test_clearing_an_expense_category_is_rejected(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    category_id = await default_expense_category(client, headers)
    payload = _transaction(account_id, category_id=str(category_id))
    created = await client.post("/v1/transactions", json=payload, headers={**headers, **_idem()})
    assert created.status_code == 200, created.text

    response = await client.patch(
        f"/v1/transactions/{payload['id']}", json={"category_id": None}, headers=headers
    )
    assert _is_category_required(response), response.text

    still = await client.get(f"/v1/transactions/{payload['id']}", headers=headers)
    assert still.json()["data"]["category_id"] == str(category_id)


async def test_editing_an_expense_without_touching_its_category_is_allowed(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    category_id = await default_expense_category(client, headers)
    payload = _transaction(account_id, category_id=str(category_id))
    await client.post("/v1/transactions", json=payload, headers={**headers, **_idem()})

    response = await client.patch(
        f"/v1/transactions/{payload['id']}", json={"description": "Súper"}, headers=headers
    )
    assert response.status_code == 200, response.text


async def test_clearing_an_income_category_is_rejected(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    income_category = await create_category(client, headers, kind="income", name="Salario")
    payload = _transaction(account_id, kind="income", category_id=str(income_category))
    created = await client.post("/v1/transactions", json=payload, headers={**headers, **_idem()})
    assert created.status_code == 200, created.text

    response = await client.patch(
        f"/v1/transactions/{payload['id']}", json={"category_id": None}, headers=headers
    )
    assert _is_category_required(response), response.text


# ---------- sincronización (push desde el teléfono)


async def test_sync_push_rejects_an_expense_without_category_as_a_conflict(
    client: AsyncClient,
) -> None:
    headers, device_id = await register_and_login_with_device(client)
    account_id = await create_account(client, headers)
    category_id = await default_expense_category(client, headers)
    without = _transaction(account_id)
    with_category = _transaction(account_id, category_id=str(category_id))

    def mutation(payload: dict[str, object]) -> dict[str, object]:
        return {
            "client_mutation_id": str(uuid.uuid4()),
            "entity_type": "transaction",
            "entity_id": payload["id"],
            "op": "upsert",
            "payload": payload,
            "client_updated_at": datetime.now(UTC).isoformat(),
        }

    response = await client.post(
        "/v1/sync/push",
        json={
            "device_id": str(device_id),
            "mutations": [mutation(without), mutation(with_category)],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    # Una mutación mala no tumba el lote: la otra se aplica.
    assert len(data["applied"]) == 1
    assert [c["reason"] for c in data["conflicts"]] == ["CATEGORY_REQUIRED"]
    assert data["conflicts"][0]["entity_id"] == without["id"]


# ---------- reglas recurrentes (suscripciones)


async def test_recurring_expense_rule_requires_category(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)

    response = await client.post("/v1/recurring-rules", json=_rule(account_id), headers=headers)
    assert _is_category_required(response), response.text


async def test_recurring_income_rule_requires_category(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)

    response = await client.post(
        "/v1/recurring-rules", json=_rule(account_id, kind="income"), headers=headers
    )
    assert _is_category_required(response), response.text


async def test_clearing_a_recurring_rule_category_is_rejected(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    category_id = await default_expense_category(client, headers)
    rule = _rule(account_id, category_id=str(category_id))
    created = await client.post("/v1/recurring-rules", json=rule, headers=headers)
    assert created.status_code == 200, created.text

    response = await client.patch(
        f"/v1/recurring-rules/{rule['id']}", json={"category_id": None}, headers=headers
    )
    assert _is_category_required(response), response.text


# ---------- planes de cuotas


async def test_installment_plan_requires_category(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)

    response = await client.post("/v1/installment-plans", json=_plan(account_id), headers=headers)
    assert _is_category_required(response), response.text


async def test_clearing_an_installment_plan_category_is_rejected(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    category_id = await default_expense_category(client, headers)
    plan = _plan(account_id, category_id=str(category_id))
    created = await client.post("/v1/installment-plans", json=plan, headers=headers)
    assert created.status_code == 200, created.text

    response = await client.patch(
        f"/v1/installment-plans/{plan['id']}", json={"category_id": None}, headers=headers
    )
    assert _is_category_required(response), response.text


# ---------- plantillas


def _template(account_id: uuid.UUID, kind: str = "expense", **extra: object) -> dict[str, object]:
    return {
        "id": str(uuid.uuid4()),
        "name": "Café",
        "account_id": str(account_id),
        "kind": kind,
        "amount_cents": 5_000,
        **extra,
    }


async def test_expense_template_requires_category(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)

    response = await client.post(
        "/v1/transaction-templates", json=_template(account_id), headers=headers
    )
    assert _is_category_required(response), response.text


async def test_income_template_requires_category(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)

    response = await client.post(
        "/v1/transaction-templates", json=_template(account_id, kind="income"), headers=headers
    )
    assert _is_category_required(response), response.text


async def test_clearing_an_expense_template_category_is_rejected(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    category_id = await default_expense_category(client, headers)
    template = _template(account_id, category_id=str(category_id))
    created = await client.post("/v1/transaction-templates", json=template, headers=headers)
    assert created.status_code == 200, created.text

    response = await client.patch(
        f"/v1/transaction-templates/{template['id']}", json={"category_id": None}, headers=headers
    )
    assert _is_category_required(response), response.text


async def test_switching_a_template_kind_while_clearing_its_category_is_rejected(
    client: AsyncClient,
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    income_category = await create_category(client, headers, kind="income", name="Salario")
    template = _template(account_id, kind="income", category_id=str(income_category))
    created = await client.post("/v1/transaction-templates", json=template, headers=headers)
    assert created.status_code == 200, created.text

    response = await client.patch(
        f"/v1/transaction-templates/{template['id']}",
        json={"kind": "expense", "category_id": None},
        headers=headers,
    )
    assert _is_category_required(response), response.text


async def test_transfers_do_not_need_a_category(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    from_account = await create_account(client, headers, initial_balance_cents=100_000)
    to_account = await create_account(client, headers, name="Destino")

    response = await client.post(
        "/v1/transactions/transfer",
        json={
            "out_transaction_id": str(uuid.uuid4()),
            "in_transaction_id": str(uuid.uuid4()),
            "from_account_id": str(from_account),
            "to_account_id": str(to_account),
            "amount_cents": 10_000,
            "date": "2026-09-05",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text
