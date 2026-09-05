from httpx import AsyncClient


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
