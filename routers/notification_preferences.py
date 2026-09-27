from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.notifications import NotificationPreferencesOut, NotificationPreferencesUpdate
from services import notification_preferences_service
from storage.models.user import User

router = APIRouter(prefix="/v1/notification-preferences", tags=["notification-preferences"])


@router.get("")
async def get_notification_preferences(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[NotificationPreferencesOut]:
    prefs = await notification_preferences_service.get_or_create(db, current_user.id)
    return ok(NotificationPreferencesOut.model_validate(prefs))


@router.patch("")
async def update_notification_preferences(
    payload: NotificationPreferencesUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[NotificationPreferencesOut]:
    prefs = await notification_preferences_service.update(db, current_user.id, payload)
    return ok(NotificationPreferencesOut.model_validate(prefs), "Preferencias actualizadas.")
