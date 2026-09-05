import uuid

from httpx import AsyncClient

from tests.api.helpers import create_account, register_and_login


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def _payment_ids() -> dict[str, str]:
    return {
        "id": str(uuid.uuid4()),
        "interest_transaction_id": str(uuid.uuid4()),
        "principal_transaction_id": str(uuid.uuid4()),
        "principal_transfer_out_id": str(uuid.uuid4()),
        "principal_transfer_in_id": str(uuid.uuid4()),
    }


async def test_create_debt_defaults_balance_to_principal(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    debt_id = uuid.uuid4()
    response = await client.post(
        "/v1/debts",
        json={
            "id": str(debt_id),
            "name": "Préstamo carro",
            "type": "auto_loan",
            "principal_cents": 5_000_000,
            "monthly_interest_rate": "0.01",
            "start_date": "2026-01-01",
            "term_months": 48,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["balance_cents"] == 5_000_000


async def test_amortization_sums_to_principal(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    debt_id = uuid.uuid4()
    await client.post(
        "/v1/debts",
        json={
            "id": str(debt_id),
            "name": "Préstamo",
            "principal_cents": 120_000,
            "monthly_interest_rate": "0.02",
            "start_date": "2026-01-01",
            "term_months": 12,
        },
        headers=headers,
    )
    response = await client.get(f"/v1/debts/{debt_id}/amortization", headers=headers)
    rows = response.json()["data"]["rows"]
    assert len(rows) == 12
    assert sum(r["principal_cents"] for r in rows) == 120_000
    assert rows[-1]["remaining_balance_cents"] == 0


async def test_amortization_without_term_months_fails(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    debt_id = uuid.uuid4()
    await client.post(
        "/v1/debts",
        json={
            "id": str(debt_id),
            "name": "Préstamo sin plazo",
            "principal_cents": 10_000,
            "start_date": "2026-01-01",
        },
        headers=headers,
    )
    response = await client.get(f"/v1/debts/{debt_id}/amortization", headers=headers)
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "DEBT_MISSING_TERM"


async def test_payment_splits_principal_and_interest_no_linked_account(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    checking = await create_account(client, headers, initial_balance_cents=1_000_000)
    debt_id = uuid.uuid4()
    await client.post(
        "/v1/debts",
        json={
            "id": str(debt_id),
            "name": "Préstamo personal",
            "principal_cents": 100_000,
            "monthly_interest_rate": "0.02",
            "start_date": "2026-01-01",
            "term_months": 12,
        },
        headers=headers,
    )

    payload = {
        **await _payment_ids(),
        "from_account_id": str(checking),
        "date": "2026-09-04",
        "total_cents": 10_000,
    }
    response = await client.post(
        f"/v1/debts/{debt_id}/payments", json=payload, headers={**headers, **_idem()}
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    # interés = 2% de 100,000 = 2,000; capital = 10,000 - 2,000 = 8,000
    assert data["interest_cents"] == 2_000
    assert data["principal_cents"] == 8_000

    debt = await client.get(f"/v1/debts/{debt_id}", headers=headers)
    assert debt.json()["data"]["balance_cents"] == 92_000

    account = await client.get(f"/v1/accounts/{checking}", headers=headers)
    # Los 10,000 completos salieron de la cuenta (interés + capital).
    assert account.json()["data"]["current_balance_cents"] == 990_000


async def test_payment_with_linked_account_transfers_principal(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    checking = await create_account(client, headers, initial_balance_cents=1_000_000)
    loan_account = await create_account(
        client, headers, account_type="loan", initial_balance_cents=100_000
    )
    debt_id = uuid.uuid4()
    await client.post(
        "/v1/debts",
        json={
            "id": str(debt_id),
            "name": "Préstamo con cuenta",
            "principal_cents": 100_000,
            "monthly_interest_rate": "0",
            "start_date": "2026-01-01",
            "term_months": 12,
            "linked_account_id": str(loan_account),
        },
        headers=headers,
    )

    payload = {
        **await _payment_ids(),
        "from_account_id": str(checking),
        "date": "2026-09-04",
        "total_cents": 10_000,
        "principal_cents": 10_000,
        "interest_cents": 0,
    }
    response = await client.post(
        f"/v1/debts/{debt_id}/payments", json=payload, headers={**headers, **_idem()}
    )
    assert response.status_code == 200, response.text

    loan = await client.get(f"/v1/accounts/{loan_account}", headers=headers)
    # Pagar una cuenta pasiva reduce lo que se debe (domain/balances.py).
    assert loan.json()["data"]["current_balance_cents"] == 90_000

    checking_acc = await client.get(f"/v1/accounts/{checking}", headers=headers)
    assert checking_acc.json()["data"]["current_balance_cents"] == 990_000


async def test_cross_user_cannot_read_debt(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, "a@example.com")
    headers_b = await register_and_login(client, "b@example.com")
    debt_id = uuid.uuid4()
    await client.post(
        "/v1/debts",
        json={
            "id": str(debt_id),
            "name": "Deuda de A",
            "principal_cents": 1000,
            "start_date": "2026-01-01",
        },
        headers=headers_a,
    )
    response = await client.get(f"/v1/debts/{debt_id}", headers=headers_b)
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "DEBT_NOT_FOUND"
