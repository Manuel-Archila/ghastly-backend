import uuid

from httpx import AsyncClient

from tests.api.helpers import register_and_login_with_device


async def test_list_devices_returns_only_own_devices(client: AsyncClient) -> None:
    headers_a, login_device_a = await register_and_login_with_device(client, "a@example.com")
    headers_b, login_device_b = await register_and_login_with_device(client, "b@example.com")

    second_device = uuid.uuid4()
    registered = await client.post(
        "/v1/devices",
        json={"id": str(second_device), "platform": "android", "app_version": "1.2.0"},
        headers=headers_a,
    )
    assert registered.status_code == 200

    own = await client.get("/v1/devices", headers=headers_a)
    assert own.status_code == 200
    ids = {d["id"] for d in own.json()["data"]}
    assert ids == {str(login_device_a), str(second_device)}
    assert str(login_device_b) not in ids

    theirs = await client.get("/v1/devices", headers=headers_b)
    assert {d["id"] for d in theirs.json()["data"]} == {str(login_device_b)}


async def test_deleted_device_disappears_from_list(client: AsyncClient) -> None:
    headers, login_device = await register_and_login_with_device(client)
    extra = uuid.uuid4()
    await client.post("/v1/devices", json={"id": str(extra), "platform": "ios"}, headers=headers)

    await client.delete(f"/v1/devices/{extra}", headers=headers)

    listing = await client.get("/v1/devices", headers=headers)
    assert [d["id"] for d in listing.json()["data"]] == [str(login_device)]
