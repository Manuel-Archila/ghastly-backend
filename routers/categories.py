from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.categories import (
    CategoryCreate,
    CategoryMergeRequest,
    CategoryOut,
    CategoryTreeOut,
    CategoryUpdate,
)
from schemas.common import ReorderRequest
from services import category_service
from storage.models.user import User

router = APIRouter(prefix="/v1/categories", tags=["categories"])


@router.get("")
async def list_categories(
    include_archived: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[list[CategoryTreeOut]]:
    tree = await category_service.list_categories_tree(
        db, current_user.id, include_archived=include_archived
    )
    return ok(tree)


@router.post("")
async def create_category(
    payload: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[CategoryOut]:
    category = await category_service.create_category(db, current_user.id, payload)
    return ok(CategoryOut.model_validate(category), "Categoría creada.")


# Rutas literales antes que "/{category_id}" (mismo motivo que en accounts.py).
@router.post("/seed")
async def seed_categories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[list[CategoryOut]]:
    created = await category_service.seed_categories(db, current_user.id)
    return ok([CategoryOut.model_validate(c) for c in created], "Semilla aplicada.")


@router.patch("/reorder")
async def reorder_categories(
    payload: ReorderRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await category_service.reorder_categories(db, current_user.id, payload.ids)
    return ok(None, "Orden actualizado.")


@router.get("/{category_id}")
async def get_category(
    category_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[CategoryOut]:
    category = await category_service.get_category(db, current_user.id, category_id)
    return ok(CategoryOut.model_validate(category))


@router.patch("/{category_id}")
async def update_category(
    category_id: UUID,
    payload: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[CategoryOut]:
    category = await category_service.update_category(db, current_user.id, category_id, payload)
    return ok(CategoryOut.model_validate(category), "Categoría actualizada.")


@router.delete("/{category_id}")
async def archive_category(
    category_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await category_service.archive_category(db, current_user.id, category_id)
    return ok(None, "Categoría archivada.")


@router.post("/{category_id}/merge")
async def merge_category(
    category_id: UUID,
    payload: CategoryMergeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await category_service.merge_categories(db, current_user.id, category_id, payload.into_id)
    return ok(None, "Categorías fusionadas.")
