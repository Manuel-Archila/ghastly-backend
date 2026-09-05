"""Orquestación de metas de ahorro.

`contribute` mueve dinero de verdad SOLO si la meta tiene `linked_account_id`
y el aporte trae `from_account_id` (se hace una transferencia normal, que
ya deja el rastro contable); si no, es un ajuste manual del progreso sin
movimiento real. En ambos casos no commitea sola — pasa por
`core/idempotency.py::handle_idempotent_write`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ConflictError, NotFoundError, ValidationAppError
from schemas.goals import GoalContributionCreate, GoalCreate, GoalOut, GoalUpdate
from schemas.transactions import TransferCreate
from services import transaction_service
from services.change_log import record_change
from storage.models.account import Account
from storage.models.goal import Goal, GoalContribution


async def _get_owned(db: AsyncSession, user_id: UUID, goal_id: UUID) -> Goal:
    goal = await db.get(Goal, goal_id)
    if goal is None or goal.user_id != user_id or goal.deleted_at is not None:
        raise NotFoundError("La meta no existe.", code="GOAL_NOT_FOUND")
    return goal


async def create_goal(db: AsyncSession, user_id: UUID, data: GoalCreate) -> Goal:
    if data.linked_account_id is not None:
        account = await db.get(Account, data.linked_account_id)
        if account is None or account.user_id != user_id or account.deleted_at is not None:
            raise NotFoundError("La cuenta enlazada no existe.", code="ACCOUNT_NOT_FOUND")

    goal = Goal(
        id=data.id,
        user_id=user_id,
        name=data.name,
        target_amount_cents=data.target_amount_cents,
        target_date=data.target_date,
        linked_account_id=data.linked_account_id,
        icon=data.icon,
    )
    db.add(goal)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("Ya existe una meta con ese id.", code="GOAL_ID_TAKEN") from exc

    await record_change(
        db,
        user_id=user_id,
        entity_type="goal",
        op="upsert",
        entity=goal,
        payload=GoalOut.model_validate(goal).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(goal)
    return goal


async def list_goals(db: AsyncSession, user_id: UUID) -> list[Goal]:
    result = await db.execute(
        select(Goal).where(Goal.user_id == user_id, Goal.deleted_at.is_(None))
    )
    return list(result.scalars().all())


async def get_goal(db: AsyncSession, user_id: UUID, goal_id: UUID) -> Goal:
    return await _get_owned(db, user_id, goal_id)


async def update_goal(db: AsyncSession, user_id: UUID, goal_id: UUID, data: GoalUpdate) -> Goal:
    goal = await _get_owned(db, user_id, goal_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(goal, field, value)
    goal.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(goal)
    return goal


async def delete_goal(db: AsyncSession, user_id: UUID, goal_id: UUID) -> None:
    goal = await _get_owned(db, user_id, goal_id)
    goal.deleted_at = datetime.now(UTC)
    goal.updated_at = datetime.now(UTC)
    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="goal",
        op="delete",
        entity=goal,
        payload={"id": str(goal.id)},
    )
    await db.commit()


async def contribute(
    db: AsyncSession, user_id: UUID, goal_id: UUID, data: GoalContributionCreate
) -> GoalContribution:
    goal = await _get_owned(db, user_id, goal_id)
    transaction_id = None

    if goal.linked_account_id is not None and data.from_account_id is not None:
        if data.transfer_out_id is None or data.transfer_in_id is None:
            raise ValidationAppError(
                "Se requieren transfer_out_id y transfer_in_id para un aporte con movimiento real.",
                code="GOAL_TRANSFER_IDS_REQUIRED",
            )
        _out_txn, in_txn = await transaction_service.transfer(
            db,
            user_id,
            TransferCreate(
                out_transaction_id=data.transfer_out_id,
                in_transaction_id=data.transfer_in_id,
                from_account_id=data.from_account_id,
                to_account_id=goal.linked_account_id,
                amount_cents=data.amount_cents,
                date=data.date,
                description=f"Aporte a meta: {goal.name}",
            ),
        )
        transaction_id = in_txn.id

    goal.current_amount_cents += data.amount_cents
    goal.updated_at = datetime.now(UTC)
    if goal.current_amount_cents >= goal.target_amount_cents:
        goal.status = "completed"

    contribution = GoalContribution(
        id=data.id,
        user_id=user_id,
        goal_id=goal.id,
        date=data.date,
        amount_cents=data.amount_cents,
        transaction_id=transaction_id,
    )
    db.add(contribution)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            "Ya existe un aporte con ese id.", code="GOAL_CONTRIBUTION_ID_TAKEN"
        ) from exc

    await record_change(
        db,
        user_id=user_id,
        entity_type="goal",
        op="upsert",
        entity=goal,
        payload=GoalOut.model_validate(goal).model_dump(mode="json"),
    )
    return contribution
