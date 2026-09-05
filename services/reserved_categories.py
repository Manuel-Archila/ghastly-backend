"""Categorías que el sistema necesita para su propio funcionamiento.

Se crean solas la primera vez que hacen falta — no dependen de que el
usuario haya corrido `/categories/seed`.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.change_log import record_change
from storage.models.category import Category

ADJUSTMENT_CATEGORY_NAME = "Ajuste de saldo"  # POST /accounts/{id}/adjust (caso 9)
DEBT_CATEGORY_NAME = "Deudas e intereses"  # ya viene en la semilla; se reusa si existe


async def get_or_create_category(
    db: AsyncSession, user_id: UUID, name: str, *, kind: str = "expense"
) -> Category:
    result = await db.execute(
        select(Category).where(
            Category.user_id == user_id,
            Category.name == name,
            Category.kind == kind,
            Category.deleted_at.is_(None),
        )
    )
    category = result.scalar_one_or_none()
    if category is not None:
        return category

    category = Category(id=uuid4(), user_id=user_id, name=name, kind=kind)
    db.add(category)
    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="category",
        op="upsert",
        entity=category,
        payload={"id": str(category.id), "name": category.name, "kind": category.kind},
    )
    return category


async def get_or_create_adjustment_category(db: AsyncSession, user_id: UUID) -> Category:
    return await get_or_create_category(db, user_id, ADJUSTMENT_CATEGORY_NAME, kind="expense")


async def get_or_create_debt_category(db: AsyncSession, user_id: UUID) -> Category:
    return await get_or_create_category(db, user_id, DEBT_CATEGORY_NAME, kind="expense")
