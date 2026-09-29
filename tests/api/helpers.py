"""Helpers compartidos por tests/api/*. No es un archivo test_*.py, pytest no lo recolecta."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from httpx import AsyncClient

PASSWORD = "supersecreto123"


async def register_and_login(
    client: AsyncClient, email: str = "user@example.com"
) -> dict[str, str]:
    headers, _device_id = await register_and_login_with_device(client, email)
    return headers


async def register_and_login_with_device(
    client: AsyncClient, email: str = "user@example.com", device_id: UUID | None = None
) -> tuple[dict[str, str], UUID]:
    device_id = device_id or uuid4()
    await client.post(
        "/v1/auth/register",
        json={"email": email, "password": PASSWORD, "name": "Usuaria de Prueba"},
    )
    response = await client.post(
        "/v1/auth/login",
        json={
            "email": email,
            "password": PASSWORD,
            "device_id": str(device_id),
            "platform": "ios",
        },
    )
    assert response.status_code == 200, response.text
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}, device_id


async def create_account(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    account_type: str = "checking",
    initial_balance_cents: int = 0,
    currency: str = "GTQ",
    name: str = "Cuenta de prueba",
    statement_day: int | None = None,
    payment_due_day: int | None = None,
    minimum_payment_percent: str | None = None,
) -> UUID:
    account_id = uuid4()
    payload: dict[str, Any] = {
        "id": str(account_id),
        "name": name,
        "type": account_type,
        "currency": currency,
        "initial_balance_cents": initial_balance_cents,
    }
    if statement_day is not None:
        payload["statement_day"] = statement_day
    if payment_due_day is not None:
        payload["payment_due_day"] = payment_due_day
    if minimum_payment_percent is not None:
        payload["minimum_payment_percent"] = minimum_payment_percent
    response = await client.post(
        "/v1/accounts",
        json=payload,
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return account_id


async def create_category(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    kind: str = "expense",
    name: str = "Categoría de prueba",
    parent_id: UUID | None = None,
) -> UUID:
    category_id = uuid4()
    payload: dict[str, Any] = {"id": str(category_id), "name": name, "kind": kind}
    if parent_id is not None:
        payload["parent_id"] = str(parent_id)
    response = await client.post("/v1/categories", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return category_id


_DEFAULT_EXPENSE_CATEGORY: dict[str, UUID] = {}


async def default_expense_category(client: AsyncClient, headers: dict[str, str]) -> UUID:
    """Una categoría de gasto por usuario, creada la primera vez que se pide.

    Un gasto no puede existir sin categoría (`domain/category_rule.py`): los tests
    que solo necesitan "un gasto válido" usan esta y los que prueban la regla
    la omiten a propósito.
    """
    key = headers["Authorization"]
    if key not in _DEFAULT_EXPENSE_CATEGORY:
        _DEFAULT_EXPENSE_CATEGORY[key] = await create_category(
            client, headers, name="Gasto de prueba"
        )
    return _DEFAULT_EXPENSE_CATEGORY[key]
