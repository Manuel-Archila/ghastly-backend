"""Cliente de Expo Push API — PLAN-backend.md §14: el frontend es Expo, así
que es el proveedor natural (resuelve la pregunta abierta sobre Expo Push
vs. APNs/FCM directo).

Un push fallido (red, HTTP, token inválido) nunca debe romper la escritura
que lo disparó ni la corrida de un job: todo error se loguea y se traga acá,
no se propaga.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from storage.models.auth import Device

logger = structlog.get_logger("core.push")

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
# Límite documentado de Expo por request.
BATCH_SIZE = 100


def _chunks(items: list[Device], size: int) -> list[list[Device]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _devices_with_token(db: AsyncSession, user_id: uuid.UUID) -> list[Device]:
    result = await db.execute(
        select(Device).where(Device.user_id == user_id, Device.push_token.is_not(None))
    )
    return list(result.scalars().all())


async def _send_batch(
    client: httpx.AsyncClient,
    devices: list[Device],
    title: str,
    body: str,
    data: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    settings = get_settings()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if settings.expo_access_token:
        headers["Authorization"] = f"Bearer {settings.expo_access_token}"

    messages = [
        {"to": device.push_token, "title": title, "body": body, "data": data or {}}
        for device in devices
    ]
    response = await client.post(EXPO_PUSH_URL, json=messages, headers=headers)
    response.raise_for_status()
    payload = response.json()
    tickets: list[dict[str, Any]] = payload.get("data", [])
    return tickets


async def _drop_unregistered_devices(
    db: AsyncSession, devices: list[Device], tickets: list[dict[str, Any]]
) -> None:
    """`flush()`, no `commit()`: este módulo no sabe si lo está llamando un
    flujo que todavía no comprometió su transacción (p. ej. el gancho
    "tras cada escritura" de presupuesto) — comprometer acá lo adelantaría.
    Quien abrió la sesión hace el commit."""
    for device, ticket in zip(devices, tickets, strict=False):
        if ticket.get("status") != "error":
            continue
        if ticket.get("details", {}).get("error") == "DeviceNotRegistered":
            await db.delete(device)
            logger.info("push_device_unregistered", device_id=str(device.id))
    await db.flush()


async def send_push_to_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> None:
    """No-op silencioso si el usuario no tiene ningún dispositivo con
    push_token (no todos habilitan notificaciones)."""
    devices = await _devices_with_token(db, user_id)
    if not devices:
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for batch in _chunks(devices, BATCH_SIZE):
                tickets = await _send_batch(client, batch, title, body, data)
                await _drop_unregistered_devices(db, batch, tickets)
    except httpx.HTTPError:
        logger.error("push_send_failed", user_id=str(user_id))
