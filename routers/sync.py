from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.sync import SyncPullOut, SyncPushRequest, SyncPushResult, SyncStatusOut
from services import sync_service
from storage.models.user import User

router = APIRouter(prefix="/v1/sync", tags=["sync"])


@router.get("/pull")
async def pull(
    since: int = 0,
    limit: int = 500,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[SyncPullOut]:
    result = await sync_service.pull(db, current_user.id, since=since, limit=limit)
    return ok(result)


@router.post("/push")
async def push(
    payload: SyncPushRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[SyncPushResult]:
    result = await sync_service.push(db, current_user.id, payload.device_id, payload.mutations)
    return ok(result)


@router.get("/status")
async def status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[SyncStatusOut]:
    server_seq = await sync_service.get_status(db, current_user.id)
    return ok(SyncStatusOut(server_seq=server_seq))
