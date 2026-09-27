"""Entrega push real (Expo Push API, PLAN-backend.md §14): cubre el wiring
de `services.push_service` + `core.push` contra Postgres real. No pega a la
red real de Expo — `core.push.httpx.AsyncClient` se reemplaza por un
cliente con `httpx.MockTransport` que graba los mensajes enviados.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from services import push_service
from storage.models.auth import Device
from tests.api.helpers import create_account, create_category, register_and_login_with_device

DEAD_TOKEN = "ExponentPushToken[dead]"


def _current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


@pytest.fixture
def sent_messages(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        messages = json.loads(request.content)
        sent.extend(messages)
        tickets = [
            {
                "status": "error",
                "message": "not registered",
                "details": {"error": "DeviceNotRegistered"},
            }
            if message["to"] == DEAD_TOKEN
            else {"status": "ok", "id": str(uuid.uuid4())}
            for message in messages
        ]
        return httpx.Response(200, json={"data": tickets})

    class _FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return sent


async def _register_push_device(
    client: AsyncClient, headers: dict[str, str], device_id: uuid.UUID, push_token: str
) -> None:
    response = await client.post(
        "/v1/devices",
        json={"id": str(device_id), "platform": "ios", "push_token": push_token},
        headers=headers,
    )
    assert response.status_code == 200, response.text


async def _create_budget(
    client: AsyncClient, headers: dict[str, str], category_id: uuid.UUID
) -> None:
    response = await client.post(
        "/v1/budgets",
        json={
            "id": str(uuid.uuid4()),
            "name": "Presupuesto de prueba",
            "items": [
                {"id": str(uuid.uuid4()), "category_id": str(category_id), "amount_cents": 100_000}
            ],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text


async def _create_expense(
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


async def test_budget_alert_over_threshold_sends_push(
    client: AsyncClient, sent_messages: list[dict[str, Any]]
) -> None:
    headers, device_id = await register_and_login_with_device(client)
    await _register_push_device(client, headers, device_id, "ExponentPushToken[abc]")
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    category_id = await create_category(client, headers, name="Alimentación")
    await _create_budget(client, headers, category_id)

    await _create_expense(client, headers, account_id, category_id, 90_000)  # 90%

    assert len(sent_messages) == 1
    assert sent_messages[0]["to"] == "ExponentPushToken[abc]"
    assert "Alimentación" in sent_messages[0]["body"]


async def test_budget_alert_same_threshold_same_month_sends_push_once(
    client: AsyncClient, sent_messages: list[dict[str, Any]]
) -> None:
    """El gancho tras cada escritura se dispara en cada gasto mientras la
    categoría siga sobre el umbral (PLAN-backend.md §9) — `budget_alerts_sent`
    evita mandar el push de nuevo por el mismo (categoría, mes, umbral)."""
    headers, device_id = await register_and_login_with_device(client)
    await _register_push_device(client, headers, device_id, "ExponentPushToken[abc]")
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    category_id = await create_category(client, headers, name="Alimentación")
    await _create_budget(client, headers, category_id)

    await _create_expense(client, headers, account_id, category_id, 90_000)  # 90%
    await _create_expense(client, headers, account_id, category_id, 1_000)  # sigue sobre 80%

    assert len(sent_messages) == 1


async def test_budget_alert_under_threshold_sends_nothing(
    client: AsyncClient, sent_messages: list[dict[str, Any]]
) -> None:
    headers, device_id = await register_and_login_with_device(client)
    await _register_push_device(client, headers, device_id, "ExponentPushToken[abc]")
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    category_id = await create_category(client, headers, name="Alimentación")
    await _create_budget(client, headers, category_id)

    await _create_expense(client, headers, account_id, category_id, 50_000)  # 50%

    assert sent_messages == []


async def test_no_push_token_registered_is_a_silent_noop(
    client: AsyncClient, sent_messages: list[dict[str, Any]]
) -> None:
    headers, _device_id = await register_and_login_with_device(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    category_id = await create_category(client, headers, name="Alimentación")
    await _create_budget(client, headers, category_id)

    await _create_expense(client, headers, account_id, category_id, 90_000)  # 90%, sin push_token

    assert sent_messages == []


async def test_budget_alert_uses_configured_threshold(
    client: AsyncClient, sent_messages: list[dict[str, Any]]
) -> None:
    headers, device_id = await register_and_login_with_device(client)
    await _register_push_device(client, headers, device_id, "ExponentPushToken[abc]")
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    category_id = await create_category(client, headers, name="Alimentación")
    await _create_budget(client, headers, category_id)
    response = await client.patch(
        "/v1/notification-preferences", json={"budget_alert_thresholds": [50]}, headers=headers
    )
    assert response.status_code == 200, response.text

    # 60% no cruza el default (80/100) pero sí el umbral configurado (50).
    await _create_expense(client, headers, account_id, category_id, 60_000)

    assert len(sent_messages) == 1
    assert "60" in sent_messages[0]["body"]


async def test_disabling_push_channel_sends_nothing(
    client: AsyncClient, sent_messages: list[dict[str, Any]]
) -> None:
    headers, device_id = await register_and_login_with_device(client)
    await _register_push_device(client, headers, device_id, "ExponentPushToken[abc]")
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    category_id = await create_category(client, headers, name="Alimentación")
    await _create_budget(client, headers, category_id)
    response = await client.patch(
        "/v1/notification-preferences", json={"channels": []}, headers=headers
    )
    assert response.status_code == 200, response.text

    await _create_expense(client, headers, account_id, category_id, 90_000)  # 90%

    assert sent_messages == []


async def test_quiet_hours_blocks_send_without_burning_dedup(
    client: AsyncClient, sent_messages: list[dict[str, Any]]
) -> None:
    from core.timezone import now_in_business_tz

    headers, device_id = await register_and_login_with_device(client)
    await _register_push_device(client, headers, device_id, "ExponentPushToken[abc]")
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    category_id = await create_category(client, headers, name="Alimentación")
    await _create_budget(client, headers, category_id)

    now = now_in_business_tz().time()
    # Ventana de silencio que cubre "ahora": 1h antes hasta 1h después.
    start = now.replace(hour=(now.hour - 1) % 24, second=0, microsecond=0)
    end = now.replace(hour=(now.hour + 1) % 24, second=0, microsecond=0)
    response = await client.patch(
        "/v1/notification-preferences",
        json={"quiet_hours_start": start.isoformat(), "quiet_hours_end": end.isoformat()},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    await _create_expense(client, headers, account_id, category_id, 90_000)  # 90%, en silencio
    assert sent_messages == []

    # Se apagan las horas de silencio: el mismo umbral, todavía sin avisar
    # este mes, sí debe mandar el push (no se quemó el dedup).
    response = await client.patch(
        "/v1/notification-preferences",
        json={"quiet_hours_start": None, "quiet_hours_end": None},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    await _create_expense(client, headers, account_id, category_id, 1_000)  # sigue sobre 80%
    assert len(sent_messages) == 1


async def test_budget_alert_dedup_survives_the_20h_job_rerun(
    client: AsyncClient,
    sent_messages: list[dict[str, Any]],
    async_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`jobs/check_budget_alerts.py` (20:00) complementa el gancho tras cada
    escritura — no debe volver a avisar el mismo (categoría, mes, umbral)
    que el gancho ya mandó."""
    import jobs.check_budget_alerts as check_budget_alerts_job

    headers, device_id = await register_and_login_with_device(client)
    await _register_push_device(client, headers, device_id, "ExponentPushToken[abc]")
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    category_id = await create_category(client, headers, name="Alimentación")
    await _create_budget(client, headers, category_id)

    await _create_expense(
        client, headers, account_id, category_id, 90_000
    )  # 90%, dispara el gancho
    assert len(sent_messages) == 1

    session_factory = async_sessionmaker(async_engine, expire_on_commit=False)
    monkeypatch.setattr(check_budget_alerts_job, "get_session_factory", lambda: session_factory)
    await check_budget_alerts_job.run()

    assert len(sent_messages) == 1


async def test_device_not_registered_ticket_deletes_device(
    client: AsyncClient,
    sent_messages: list[dict[str, Any]],
    async_engine: AsyncEngine,
) -> None:
    headers, device_id = await register_and_login_with_device(client)
    await _register_push_device(client, headers, device_id, DEAD_TOKEN)

    me_response = await client.get("/v1/auth/me", headers=headers)
    user_id = uuid.UUID(me_response.json()["data"]["id"])

    session_factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with session_factory() as db:
        await push_service.notify_card_statement(db, user_id, "BAC Visa")
        await db.commit()

        result = await db.execute(select(Device).where(Device.id == device_id))
        assert result.scalar_one_or_none() is None
