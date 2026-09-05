from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.auth import DeviceOut, DeviceUpsertRequest
from services import auth_service
from storage.models.user import User

router = APIRouter(prefix="/v1/devices", tags=["devices"])


@router.post("")
async def register_device(
    payload: DeviceUpsertRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[DeviceOut]:
    device = await auth_service.register_device(db, current_user.id, payload)
    return ok(DeviceOut.model_validate(device), "Dispositivo registrado.")


@router.delete("/{device_id}")
async def delete_device(
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    # Test de acceso cruzado obligatorio (CLAUDE.md): el service filtra por
    # user_id y devuelve 404 si el dispositivo es de otro usuario.
    await auth_service.delete_device(db, current_user.id, device_id)
    return ok(None, "Dispositivo eliminado.")
