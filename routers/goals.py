from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.idempotency import IDEMPOTENCY_KEY_HEADER, handle_idempotent_write
from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.goals import (
    GoalContributionCreate,
    GoalContributionOut,
    GoalCreate,
    GoalOut,
    GoalUpdate,
)
from services import goal_service
from storage.models.user import User

router = APIRouter(prefix="/v1/goals", tags=["goals"])


@router.get("")
async def list_goals(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[list[GoalOut]]:
    goals = await goal_service.list_goals(db, current_user.id)
    return ok([GoalOut.model_validate(g) for g in goals])


@router.post("")
async def create_goal(
    payload: GoalCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[GoalOut]:
    goal = await goal_service.create_goal(db, current_user.id, payload)
    return ok(GoalOut.model_validate(goal), "Meta creada.")


@router.get("/{goal_id}")
async def get_goal(
    goal_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[GoalOut]:
    goal = await goal_service.get_goal(db, current_user.id, goal_id)
    return ok(GoalOut.model_validate(goal))


@router.patch("/{goal_id}")
async def update_goal(
    goal_id: UUID,
    payload: GoalUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[GoalOut]:
    goal = await goal_service.update_goal(db, current_user.id, goal_id, payload)
    return ok(GoalOut.model_validate(goal), "Meta actualizada.")


@router.delete("/{goal_id}")
async def delete_goal(
    goal_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await goal_service.delete_goal(db, current_user.id, goal_id)
    return ok(None, "Meta eliminada.")


@router.post("/{goal_id}/contribute", response_model=None)
async def contribute(
    goal_id: UUID,
    request: Request,
    payload: GoalContributionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[GoalContributionOut] | JSONResponse:
    raw_body = await request.body()

    async def _perform() -> GoalContributionOut:
        contribution = await goal_service.contribute(db, current_user.id, goal_id, payload)
        return GoalContributionOut.model_validate(contribution)

    return await handle_idempotent_write(
        db,
        user_id=current_user.id,
        idempotency_key=request.headers.get(IDEMPOTENCY_KEY_HEADER),
        raw_body=raw_body,
        endpoint=f"POST /v1/goals/{goal_id}/contribute",
        perform=_perform,
        message="Aporte registrado.",
    )
