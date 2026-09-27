from httpx import AsyncClient

from tests.api.helpers import register_and_login


async def test_get_creates_defaults_on_first_read(client: AsyncClient) -> None:
    headers = await register_and_login(client)

    response = await client.get("/v1/notification-preferences", headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert sorted(data["budget_alert_thresholds"]) == [80, 100]
    assert data["due_reminder_days"] == 3
    assert data["quiet_hours_start"] is None
    assert data["quiet_hours_end"] is None
    assert data["channels"] == ["push"]


async def test_patch_updates_only_given_fields(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    await client.get("/v1/notification-preferences", headers=headers)

    response = await client.patch(
        "/v1/notification-preferences",
        json={"due_reminder_days": 5},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["due_reminder_days"] == 5
    assert sorted(data["budget_alert_thresholds"]) == [80, 100]  # sin cambios


async def test_patch_sets_quiet_hours(client: AsyncClient) -> None:
    headers = await register_and_login(client)

    response = await client.patch(
        "/v1/notification-preferences",
        json={"quiet_hours_start": "22:00:00", "quiet_hours_end": "07:00:00"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["quiet_hours_start"] == "22:00:00"
    assert data["quiet_hours_end"] == "07:00:00"


async def test_patch_can_clear_quiet_hours(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    await client.patch(
        "/v1/notification-preferences",
        json={"quiet_hours_start": "22:00:00", "quiet_hours_end": "07:00:00"},
        headers=headers,
    )

    response = await client.patch(
        "/v1/notification-preferences",
        json={"quiet_hours_start": None, "quiet_hours_end": None},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["quiet_hours_start"] is None
    assert data["quiet_hours_end"] is None


async def test_patch_rejects_quiet_hours_start_without_end(client: AsyncClient) -> None:
    headers = await register_and_login(client)

    response = await client.patch(
        "/v1/notification-preferences",
        json={"quiet_hours_start": "22:00:00"},
        headers=headers,
    )

    assert response.status_code == 422, response.text
    assert response.json()["data"]["code"] == "INVALID_QUIET_HOURS"


async def test_patch_rejects_empty_thresholds(client: AsyncClient) -> None:
    headers = await register_and_login(client)

    response = await client.patch(
        "/v1/notification-preferences",
        json={"budget_alert_thresholds": []},
        headers=headers,
    )

    assert response.status_code == 422, response.text
    assert response.json()["data"]["code"] == "INVALID_THRESHOLDS"


async def test_patch_rejects_threshold_out_of_range(client: AsyncClient) -> None:
    headers = await register_and_login(client)

    response = await client.patch(
        "/v1/notification-preferences",
        json={"budget_alert_thresholds": [0, 150]},
        headers=headers,
    )

    assert response.status_code == 422, response.text
    assert response.json()["data"]["code"] == "INVALID_THRESHOLDS"


async def test_patch_rejects_duplicate_thresholds(client: AsyncClient) -> None:
    headers = await register_and_login(client)

    response = await client.patch(
        "/v1/notification-preferences",
        json={"budget_alert_thresholds": [80, 80]},
        headers=headers,
    )

    assert response.status_code == 422, response.text
    assert response.json()["data"]["code"] == "INVALID_THRESHOLDS"


async def test_patch_rejects_negative_due_reminder_days(client: AsyncClient) -> None:
    headers = await register_and_login(client)

    response = await client.patch(
        "/v1/notification-preferences",
        json={"due_reminder_days": -1},
        headers=headers,
    )

    assert response.status_code == 422, response.text
    assert response.json()["data"]["code"] == "INVALID_DUE_REMINDER_DAYS"


async def test_patch_rejects_unsupported_channel(client: AsyncClient) -> None:
    headers = await register_and_login(client)

    response = await client.patch(
        "/v1/notification-preferences",
        json={"channels": ["push", "email"]},
        headers=headers,
    )

    assert response.status_code == 422, response.text
    assert response.json()["data"]["code"] == "INVALID_CHANNEL"


async def test_preferences_are_isolated_between_users(client: AsyncClient) -> None:
    headers_a = await register_and_login(client, email="a@example.com")
    headers_b = await register_and_login(client, email="b@example.com")

    await client.patch(
        "/v1/notification-preferences", json={"due_reminder_days": 10}, headers=headers_a
    )

    response_b = await client.get("/v1/notification-preferences", headers=headers_b)
    assert response_b.json()["data"]["due_reminder_days"] == 3  # default, no lo de A
