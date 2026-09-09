from httpx import AsyncClient

from tests.api.helpers import register_and_login


async def test_health() -> None:
    from httpx import ASGITransport

    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["is_success"] is True
    assert body["data"]["status"] == "ok"


async def test_health_ready(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ready"


async def test_info_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/info")
    assert response.status_code == 401


async def test_info_returns_version_and_latest_applied_migration(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    response = await client.get("/info", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["version"] == "0.1.0"
    # La última migración aplicada por la fixture de test — crece con cada
    # V1_N nueva, así que solo se valida la forma, no el número exacto.
    assert data["migration_version"] is not None
    assert data["migration_name"] is not None
