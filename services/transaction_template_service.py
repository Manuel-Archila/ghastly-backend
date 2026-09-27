"""Orquestación de plantillas de gasto frecuente (PLAN-backend.md §5/§8).

Una plantilla es solo datos para que el cliente prellene `POST
/transactions` — no hay una relación contable persistida entre la
transacción resultante y su plantilla (a diferencia de `refund_of_id` o
`receivable_id`). `mark_used` lo llama `transaction_service.create_transaction`
cuando el cliente manda `template_id` en el body, dentro de la misma
transacción de DB; no commitea sola.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ConflictError, NotFoundError, ValidationAppError
from schemas.transaction_templates import (
    TransactionTemplateCreate,
    TransactionTemplateOut,
    TransactionTemplateUpdate,
)
from services.change_log import record_change
from storage.models.account import Account
from storage.models.category import Category
from storage.models.transaction_template import TransactionTemplate


async def _get_owned(db: AsyncSession, user_id: UUID, template_id: UUID) -> TransactionTemplate:
    template = await db.get(TransactionTemplate, template_id)
    if template is None or template.user_id != user_id or template.deleted_at is not None:
        raise NotFoundError("La plantilla no existe.", code="TEMPLATE_NOT_FOUND")
    return template


async def _require_account(db: AsyncSession, user_id: UUID, account_id: UUID) -> None:
    account = await db.get(Account, account_id)
    if account is None or account.user_id != user_id or account.deleted_at is not None:
        raise NotFoundError("La cuenta no existe.", code="ACCOUNT_NOT_FOUND")


async def _require_category_for_kind(
    db: AsyncSession, user_id: UUID, category_id: UUID, kind: str
) -> None:
    category = await db.get(Category, category_id)
    if category is None or category.user_id != user_id or category.deleted_at is not None:
        raise NotFoundError("La categoría no existe.", code="CATEGORY_NOT_FOUND")
    if category.kind != kind:
        raise ValidationAppError(
            "La categoría no coincide con el tipo de movimiento (ingreso/gasto).",
            field="category_id",
            code="CATEGORY_KIND_MISMATCH",
        )


async def create_template(
    db: AsyncSession, user_id: UUID, data: TransactionTemplateCreate
) -> TransactionTemplate:
    await _require_account(db, user_id, data.account_id)
    if data.category_id is not None:
        await _require_category_for_kind(db, user_id, data.category_id, data.kind)

    template = TransactionTemplate(
        id=data.id,
        user_id=user_id,
        name=data.name,
        account_id=data.account_id,
        category_id=data.category_id,
        kind=data.kind,
        amount_cents=data.amount_cents,
        description=data.description,
    )
    db.add(template)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            "Ya existe una plantilla con ese id.", code="TEMPLATE_ID_TAKEN"
        ) from exc

    await record_change(
        db,
        user_id=user_id,
        entity_type="transaction_template",
        op="upsert",
        entity=template,
        payload=TransactionTemplateOut.model_validate(template).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(template)
    return template


async def list_templates(db: AsyncSession, user_id: UUID) -> list[TransactionTemplate]:
    """Ordenadas por `use_count` desc (PLAN-backend.md §8) — las más usadas
    primero, para que la captura rápida las muestre arriba."""
    result = await db.execute(
        select(TransactionTemplate)
        .where(TransactionTemplate.user_id == user_id, TransactionTemplate.deleted_at.is_(None))
        .order_by(
            TransactionTemplate.use_count.desc(),
            TransactionTemplate.last_used_at.desc().nullslast(),
            TransactionTemplate.name,
        )
    )
    return list(result.scalars().all())


async def get_template(db: AsyncSession, user_id: UUID, template_id: UUID) -> TransactionTemplate:
    return await _get_owned(db, user_id, template_id)


async def update_template(
    db: AsyncSession, user_id: UUID, template_id: UUID, data: TransactionTemplateUpdate
) -> TransactionTemplate:
    template = await _get_owned(db, user_id, template_id)
    changes = data.model_dump(exclude_unset=True)

    if "account_id" in changes:
        await _require_account(db, user_id, changes["account_id"])

    # Cambiar el tipo también obliga a revisar la categoría que ya tenía.
    new_kind = changes.get("kind", template.kind)
    new_category_id = changes.get("category_id", template.category_id)
    if new_category_id is not None and ("category_id" in changes or "kind" in changes):
        await _require_category_for_kind(db, user_id, new_category_id, new_kind)

    for field, value in changes.items():
        setattr(template, field, value)
    template.updated_at = datetime.now(UTC)
    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="transaction_template",
        op="upsert",
        entity=template,
        payload=TransactionTemplateOut.model_validate(template).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(template)
    return template


async def delete_template(db: AsyncSession, user_id: UUID, template_id: UUID) -> None:
    template = await _get_owned(db, user_id, template_id)
    template.deleted_at = datetime.now(UTC)
    template.updated_at = datetime.now(UTC)
    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="transaction_template",
        op="delete",
        entity=template,
        payload={"id": str(template.id)},
    )
    await db.commit()


async def mark_used(db: AsyncSession, user_id: UUID, template_id: UUID) -> None:
    template = await _get_owned(db, user_id, template_id)
    template.use_count += 1
    template.last_used_at = datetime.now(UTC)
    template.updated_at = template.last_used_at
    await record_change(
        db,
        user_id=user_id,
        entity_type="transaction_template",
        op="upsert",
        entity=template,
        payload=TransactionTemplateOut.model_validate(template).model_dump(mode="json"),
    )
