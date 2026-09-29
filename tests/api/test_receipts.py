import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

import core.object_storage as object_storage
from config import Settings
from tests.api.helpers import create_account, default_expense_category, register_and_login


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def _current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


@pytest.fixture
def configured_object_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """generate_presigned_url firma localmente con las credenciales — no
    hace red hacia R2, así que unas credenciales de mentira alcanzan para
    probar todo el flujo sin mockear boto3."""
    fake_settings = Settings(
        aws_access_key_id="test-key",
        aws_secret_access_key="test-secret",
        aws_region="auto",
        s3_endpoint_url="https://test-account.r2.cloudflarestorage.com",
        s3_bucket_receipts="ghastly-test-receipts",
    )
    monkeypatch.setattr(object_storage, "get_settings", lambda: fake_settings)


@pytest.fixture
def unconfigured_object_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    unconfigured_settings = Settings(
        aws_access_key_id=None, aws_secret_access_key=None, s3_bucket_receipts=None
    )
    monkeypatch.setattr(object_storage, "get_settings", lambda: unconfigured_settings)


async def _create_expense(
    client: AsyncClient, headers: dict[str, str], account_id: uuid.UUID
) -> uuid.UUID:
    transaction_id = uuid.uuid4()
    response = await client.post(
        "/v1/transactions",
        json={
            "id": str(transaction_id),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(await default_expense_category(client, headers)),
            "amount_cents": 5_000,
            "date": f"{_current_month()}-05",
        },
        headers={**headers, **_idem()},
    )
    assert response.status_code == 200, response.text
    return transaction_id


async def test_request_upload_returns_presigned_url_and_sets_receipt_key(
    client: AsyncClient, configured_object_storage: None
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    transaction_id = await _create_expense(client, headers, account_id)

    response = await client.post(
        f"/v1/transactions/{transaction_id}/receipt",
        json={"content_type": "image/jpeg"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["receipt_key"].startswith("receipts/")
    assert data["receipt_key"].endswith(str(transaction_id))
    assert data["upload_url"].startswith("https://test-account.r2.cloudflarestorage.com/")
    assert data["expires_in"] == 300

    txn = await client.get(f"/v1/transactions/{transaction_id}", headers=headers)
    assert txn.json()["data"]["receipt_key"] == data["receipt_key"]


async def test_requesting_upload_twice_reuses_same_key(
    client: AsyncClient, configured_object_storage: None
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    transaction_id = await _create_expense(client, headers, account_id)

    first = await client.post(
        f"/v1/transactions/{transaction_id}/receipt",
        json={"content_type": "image/jpeg"},
        headers=headers,
    )
    second = await client.post(
        f"/v1/transactions/{transaction_id}/receipt",
        json={"content_type": "image/png"},
        headers=headers,
    )
    assert first.json()["data"]["receipt_key"] == second.json()["data"]["receipt_key"]


async def test_get_download_url_after_requesting_upload(
    client: AsyncClient, configured_object_storage: None
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    transaction_id = await _create_expense(client, headers, account_id)

    await client.post(
        f"/v1/transactions/{transaction_id}/receipt",
        json={"content_type": "image/jpeg"},
        headers=headers,
    )

    response = await client.get(f"/v1/transactions/{transaction_id}/receipt", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["download_url"].startswith("https://test-account.r2.cloudflarestorage.com/")
    assert data["expires_in"] == 300


async def test_get_download_url_without_receipt_returns_404(
    client: AsyncClient, configured_object_storage: None
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    transaction_id = await _create_expense(client, headers, account_id)

    response = await client.get(f"/v1/transactions/{transaction_id}/receipt", headers=headers)
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "RECEIPT_NOT_FOUND"


async def test_request_upload_for_unknown_transaction_returns_404(
    client: AsyncClient, configured_object_storage: None
) -> None:
    headers = await register_and_login(client)
    response = await client.post(
        f"/v1/transactions/{uuid.uuid4()}/receipt",
        json={"content_type": "image/jpeg"},
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "TRANSACTION_NOT_FOUND"


async def test_cross_user_cannot_request_upload_or_download(
    client: AsyncClient, configured_object_storage: None
) -> None:
    headers_a = await register_and_login(client, email="receipt-a@example.com")
    headers_b = await register_and_login(client, email="receipt-b@example.com")
    account_a = await create_account(client, headers_a, initial_balance_cents=1_000_000)
    transaction_id = await _create_expense(client, headers_a, account_a)

    upload_response = await client.post(
        f"/v1/transactions/{transaction_id}/receipt",
        json={"content_type": "image/jpeg"},
        headers=headers_b,
    )
    assert upload_response.status_code == 404
    assert upload_response.json()["data"]["code"] == "TRANSACTION_NOT_FOUND"

    download_response = await client.get(
        f"/v1/transactions/{transaction_id}/receipt", headers=headers_b
    )
    assert download_response.status_code == 404
    assert download_response.json()["data"]["code"] == "TRANSACTION_NOT_FOUND"


async def test_request_upload_without_object_storage_configured_returns_503(
    client: AsyncClient, unconfigured_object_storage: None
) -> None:
    headers = await register_and_login(client)
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    transaction_id = await _create_expense(client, headers, account_id)

    response = await client.post(
        f"/v1/transactions/{transaction_id}/receipt",
        json={"content_type": "image/jpeg"},
        headers=headers,
    )
    assert response.status_code == 503
    assert response.json()["data"]["code"] == "OBJECT_STORAGE_NOT_CONFIGURED"
