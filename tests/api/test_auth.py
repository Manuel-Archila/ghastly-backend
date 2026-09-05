import uuid
from typing import Any, cast

from httpx import AsyncClient

PASSWORD = "supersecreto123"


async def _register(client: AsyncClient, email: str, name: str = "Alejandro") -> dict[str, Any]:
    response = await client.post(
        "/v1/auth/register",
        json={"email": email, "password": PASSWORD, "name": name},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json()["data"])


async def _login(client: AsyncClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    response = await client.post(
        "/v1/auth/login",
        json={
            "email": email,
            "password": password,
            "device_id": str(uuid.uuid4()),
            "platform": "ios",
        },
    )
    body = cast(dict[str, Any], response.json())
    return body | {"status_code": response.status_code}


async def test_register_creates_user(client: AsyncClient) -> None:
    user = await _register(client, "ale@example.com")
    assert user["email"] == "ale@example.com"
    assert user["base_currency"] == "GTQ"
    assert user["timezone"] == "America/Guatemala"
    assert "password" not in user
    assert "password_hash" not in user


async def test_register_duplicate_email_is_conflict(client: AsyncClient) -> None:
    await _register(client, "dup@example.com")
    response = await client.post(
        "/v1/auth/register",
        json={"email": "dup@example.com", "password": PASSWORD, "name": "Otra vez"},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["is_success"] is False
    assert body["data"]["code"] == "EMAIL_TAKEN"


async def test_register_rejects_short_password(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/auth/register",
        json={"email": "corta@example.com", "password": "123", "name": "X"},
    )
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "VALIDATION_ERROR"


async def test_login_with_wrong_password_is_unauthorized(client: AsyncClient) -> None:
    await _register(client, "ale@example.com")
    body = await _login(client, "ale@example.com", password="incorrecta")
    assert body["status_code"] == 401
    assert body["data"]["code"] == "INVALID_CREDENTIALS"


async def test_login_returns_tokens_and_me_works(client: AsyncClient) -> None:
    await _register(client, "ale@example.com")
    body = await _login(client, "ale@example.com")
    assert body["status_code"] == 200
    access_token = body["data"]["access_token"]

    me = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me.status_code == 200
    assert me.json()["data"]["email"] == "ale@example.com"


async def test_me_without_token_is_unauthorized(client: AsyncClient) -> None:
    response = await client.get("/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["data"]["code"] == "MISSING_TOKEN"


async def test_refresh_rotates_token(client: AsyncClient) -> None:
    await _register(client, "ale@example.com")
    login_body = await _login(client, "ale@example.com")
    refresh_token = login_body["data"]["refresh_token"]

    response = await client.post("/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 200
    new_refresh = response.json()["data"]["refresh_token"]
    assert new_refresh != refresh_token


async def test_refresh_reuse_revokes_whole_device_family(client: AsyncClient) -> None:
    await _register(client, "ale@example.com")
    login_body = await _login(client, "ale@example.com")
    refresh_1 = login_body["data"]["refresh_token"]

    rotated = await client.post("/v1/auth/refresh", json={"refresh_token": refresh_1})
    refresh_2 = rotated.json()["data"]["refresh_token"]

    # Reusar el refresh #1 ya rotado -> reuso detectado.
    reuse = await client.post("/v1/auth/refresh", json={"refresh_token": refresh_1})
    assert reuse.status_code == 401
    assert reuse.json()["data"]["code"] == "REFRESH_REUSE_DETECTED"

    # El #2, que era legítimo, también queda revocado: toda la familia cae.
    also_revoked = await client.post("/v1/auth/refresh", json={"refresh_token": refresh_2})
    assert also_revoked.status_code == 401
    assert also_revoked.json()["data"]["code"] == "REFRESH_REUSE_DETECTED"


async def test_device_cross_user_access_is_denied(client: AsyncClient) -> None:
    """Test de acceso cruzado obligatorio (CLAUDE.md)."""
    await _register(client, "a@example.com")
    await _register(client, "b@example.com")
    login_a = await _login(client, "a@example.com")
    login_b = await _login(client, "b@example.com")
    token_b = login_b["data"]["access_token"]
    token_a = login_a["data"]["access_token"]
    new_device_id = str(uuid.uuid4())
    reg = await client.post(
        "/v1/devices",
        json={"id": new_device_id, "platform": "ios"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert reg.status_code == 200

    denied = await client.delete(
        f"/v1/devices/{new_device_id}", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert denied.status_code == 404
    assert denied.json()["data"]["code"] == "DEVICE_NOT_FOUND"

    allowed = await client.delete(
        f"/v1/devices/{new_device_id}", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert allowed.status_code == 200


async def test_login_rate_limited_after_five_per_minute(client: AsyncClient) -> None:
    await _register(client, "ale@example.com")
    statuses = []
    for _ in range(6):
        body = await _login(client, "ale@example.com", password="incorrecta")
        statuses.append(body["status_code"])
    assert statuses[:5] == [401, 401, 401, 401, 401]
    assert statuses[5] == 429
