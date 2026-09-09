from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.transaction_templates import TransactionTemplateCreate, TransactionTemplateOut
from services import transaction_template_service
from storage.models.user import User

router = APIRouter(prefix="/v1/transaction-templates", tags=["transaction-templates"])


@router.get("")
async def list_templates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[list[TransactionTemplateOut]]:
    templates = await transaction_template_service.list_templates(db, current_user.id)
    return ok([TransactionTemplateOut.model_validate(t) for t in templates])


@router.post("")
async def create_template(
    payload: TransactionTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[TransactionTemplateOut]:
    template = await transaction_template_service.create_template(db, current_user.id, payload)
    return ok(TransactionTemplateOut.model_validate(template), "Plantilla creada.")


@router.delete("/{template_id}")
async def delete_template(
    template_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await transaction_template_service.delete_template(db, current_user.id, template_id)
    return ok(None, "Plantilla eliminada.")
