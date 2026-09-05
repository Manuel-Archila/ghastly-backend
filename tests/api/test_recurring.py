import uuid
from datetime import date, timedelta

from httpx import AsyncClient

from tests.api.helpers import create_account, create_category, register_and_login


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def _create_rule(
    client: AsyncClient,
    headers: dict[str, str],
    account_id: uuid.UUID,
    *,
    amount_cents: int = 8_900,
    auto_create: bool = False,
    next_due_date: str = "2026-09-10",
) -> uuid.UUID:
    rule_id = uuid.uuid4()
    response = await client.post(
        "/v1/recurring-rules",
        json={
            "id": str(rule_id),
            "account_id": str(account_id),
            "kind": "expense",
            "name": "Netflix",
            "amount_cents": amount_cents,
            "frequency": "monthly",
            "next_due_date": next_due_date,
            "auto_create": auto_create,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return rule_id


async def test_confirm_rule_creates_transaction_and_advances_due_date(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=100_000)
    rule_id = await _create_rule(client, headers, account_id, next_due_date="2026-09-10")

    response = await client.post(
        f"/v1/recurring-rules/{rule_id}/confirm",
        json={"id": str(uuid.uuid4()), "date": "2026-09-10"},
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["kind"] == "expense"

    rule = await client.get(f"/v1/recurring-rules/{rule_id}", headers=headers)
    assert rule.json()["data"]["next_due_date"] == "2026-10-10"

    account = await client.get(f"/v1/accounts/{account_id}", headers=headers)
    assert account.json()["data"]["current_balance_cents"] == 100_000 - 8_900


async def test_pause_and_resume(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    rule_id = await _create_rule(client, headers, account_id)

    paused = await client.post(f"/v1/recurring-rules/{rule_id}/pause", headers=headers)
    assert paused.json()["data"]["status"] == "paused"

    confirm_while_paused = await client.post(
        f"/v1/recurring-rules/{rule_id}/confirm",
        json={"id": str(uuid.uuid4())},
        headers={**headers, **_idem()},
    )
    assert confirm_while_paused.status_code == 409
    assert confirm_while_paused.json()["data"]["code"] == "RECURRING_RULE_NOT_ACTIVE"

    resumed = await client.post(f"/v1/recurring-rules/{rule_id}/resume", headers=headers)
    assert resumed.json()["data"]["status"] == "active"


async def test_skip_next_advances_without_creating_transaction(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=50_000)
    rule_id = await _create_rule(client, headers, account_id, next_due_date="2026-09-10")

    await client.post(f"/v1/recurring-rules/{rule_id}/skip-next", headers=headers)
    rule = await client.get(f"/v1/recurring-rules/{rule_id}", headers=headers)
    assert rule.json()["data"]["next_due_date"] == "2026-10-10"

    account = await client.get(f"/v1/accounts/{account_id}", headers=headers)
    assert account.json()["data"]["current_balance_cents"] == 50_000


async def test_subscriptions_summary_computes_monthly_equivalent(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    await _create_rule(client, headers, account_id, amount_cents=8_900)

    summary = await client.get("/v1/subscriptions/summary", headers=headers)
    data = summary.json()["data"]
    assert data["total_monthly_cents"] == 8_900
    assert data["total_annualized_cents"] == 8_900 * 12
    assert data["items"][0]["monthly_equivalent_cents"] == 8_900


async def test_subscriptions_summary_detects_price_increase(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=100_000)
    rule_id = await _create_rule(
        client, headers, account_id, amount_cents=7_900, next_due_date="2026-09-01"
    )
    await client.post(
        f"/v1/recurring-rules/{rule_id}/confirm",
        json={"id": str(uuid.uuid4()), "date": "2026-09-01"},
        headers={**headers, **_idem()},
    )
    await client.patch(
        f"/v1/recurring-rules/{rule_id}", json={"amount_cents": 8_900}, headers=headers
    )
    await client.post(
        f"/v1/recurring-rules/{rule_id}/confirm",
        json={"id": str(uuid.uuid4()), "date": "2026-10-01"},
        headers={**headers, **_idem()},
    )

    summary = await client.get("/v1/subscriptions/summary", headers=headers)
    item = summary.json()["data"]["items"][0]
    assert item["price_increased"] is True


async def test_subscriptions_summary_flags_cancel_candidate(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=100_000)
    old_due_date = (date.today() - timedelta(days=90)).isoformat()
    rule_id = await _create_rule(client, headers, account_id, next_due_date=old_due_date)
    await client.post(
        f"/v1/recurring-rules/{rule_id}/confirm",
        json={"id": str(uuid.uuid4()), "date": old_due_date},
        headers={**headers, **_idem()},
    )

    summary = await client.get("/v1/subscriptions/summary", headers=headers)
    item = summary.json()["data"]["items"][0]
    assert item["cancel_candidate"] is True


async def test_cross_user_cannot_read_rule(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, "a@example.com")
    headers_b = await register_and_login(client, "b@example.com")
    account_id = await create_account(client, headers_a)
    rule_id = await _create_rule(client, headers_a, account_id)

    response = await client.get(f"/v1/recurring-rules/{rule_id}", headers=headers_b)
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "RECURRING_RULE_NOT_FOUND"


async def test_create_rule_with_category(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers)
    category_id = await create_category(client, headers, name="Entretenimiento")
    rule_id = uuid.uuid4()
    response = await client.post(
        "/v1/recurring-rules",
        json={
            "id": str(rule_id),
            "account_id": str(account_id),
            "category_id": str(category_id),
            "kind": "expense",
            "name": "Spotify",
            "amount_cents": 4_500,
            "frequency": "monthly",
            "next_due_date": "2026-09-05",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["category_id"] == str(category_id)
