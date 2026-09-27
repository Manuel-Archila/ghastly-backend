"""Orquestación de categorías. La regla de dos niveles vive en
`domain/categories.py`; este service solo la aplica."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ConflictError, NotFoundError, ValidationAppError
from domain.categories import CategoryError, validate_category_depth
from schemas.categories import CategoryCreate, CategoryOut, CategoryTreeOut, CategoryUpdate
from services import budget_service
from services.change_log import record_change
from storage.models.category import Category
from storage.models.transaction import Transaction

# PLAN-finanzas-app.md §3 — semilla inicial. (parent, kind, [hijos])
_SEED: list[tuple[str, str, list[str]]] = [
    ("Vivienda", "expense", []),
    ("Alimentación", "expense", []),
    ("Transporte", "expense", []),
    ("Salud", "expense", []),
    ("Educación", "expense", []),
    ("Servicios", "expense", ["Luz", "Agua", "Internet", "Teléfono"]),
    ("Entretenimiento", "expense", []),
    ("Ropa", "expense", []),
    ("Mascotas", "expense", []),
    ("Deudas e intereses", "expense", []),
    ("Impuestos", "expense", []),
    ("Ahorro e inversión", "expense", []),
    ("Regalos y donaciones", "expense", []),
    ("Otros", "expense", []),
    ("Salario", "income", []),
    ("Honorarios/Freelance", "income", []),
    ("Ventas", "income", []),
    ("Intereses", "income", []),
    ("Reembolsos", "income", []),
    ("Otros", "income", []),
]


async def _get_owned(db: AsyncSession, user_id: UUID, category_id: UUID) -> Category:
    category = await db.get(Category, category_id)
    if category is None or category.user_id != user_id or category.deleted_at is not None:
        raise NotFoundError("La categoría no existe.", code="CATEGORY_NOT_FOUND")
    return category


async def create_category(db: AsyncSession, user_id: UUID, data: CategoryCreate) -> Category:
    parent: Category | None = None
    if data.parent_id is not None:
        parent = await _get_owned(db, user_id, data.parent_id)
        try:
            validate_category_depth(chosen_parent_has_parent=parent.parent_id is not None)
        except CategoryError as exc:
            raise ValidationAppError(str(exc), code="CATEGORY_TOO_DEEP") from exc
        if parent.kind != data.kind:
            raise ValidationAppError(
                "La subcategoría debe tener el mismo tipo (ingreso/gasto) que su categoría padre.",
                code="CATEGORY_KIND_MISMATCH",
            )

    category = Category(
        id=data.id,
        user_id=user_id,
        name=data.name,
        kind=data.kind,
        parent_id=data.parent_id,
        icon=data.icon,
        color=data.color,
        is_tax_deductible=data.is_tax_deductible,
        sort_order=data.sort_order,
    )
    db.add(category)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            "Ya existe una categoría con ese id.", code="CATEGORY_ID_TAKEN"
        ) from exc

    await record_change(
        db,
        user_id=user_id,
        entity_type="category",
        op="upsert",
        entity=category,
        payload=CategoryOut.model_validate(category).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(category)
    return category


async def list_categories(
    db: AsyncSession, user_id: UUID, *, include_archived: bool = False
) -> list[Category]:
    stmt = select(Category).where(Category.user_id == user_id, Category.deleted_at.is_(None))
    if not include_archived:
        stmt = stmt.where(Category.is_archived.is_(False))
    stmt = stmt.order_by(Category.sort_order, Category.name)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_categories_tree(
    db: AsyncSession, user_id: UUID, *, include_archived: bool = False
) -> list[CategoryTreeOut]:
    flat = await list_categories(db, user_id, include_archived=include_archived)
    by_id = {c.id: CategoryTreeOut.model_validate(c) for c in flat}
    roots: list[CategoryTreeOut] = []
    for category in flat:
        node = by_id[category.id]
        if category.parent_id is not None and category.parent_id in by_id:
            by_id[category.parent_id].children.append(CategoryOut.model_validate(category))
        elif category.parent_id is None:
            roots.append(node)
    return roots


async def get_category(db: AsyncSession, user_id: UUID, category_id: UUID) -> Category:
    return await _get_owned(db, user_id, category_id)


async def update_category(
    db: AsyncSession, user_id: UUID, category_id: UUID, data: CategoryUpdate
) -> Category:
    category = await _get_owned(db, user_id, category_id)
    updates = data.model_dump(exclude_unset=True)

    if "parent_id" in updates and updates["parent_id"] is not None:
        if updates["parent_id"] == category.id:
            raise ValidationAppError(
                "Una categoría no puede ser su propia madre.", code="CATEGORY_TOO_DEEP"
            )
        parent = await _get_owned(db, user_id, updates["parent_id"])
        try:
            validate_category_depth(chosen_parent_has_parent=parent.parent_id is not None)
        except CategoryError as exc:
            raise ValidationAppError(str(exc), code="CATEGORY_TOO_DEEP") from exc
        # Ascender un padre con hijos propios a subcategoría los dejaría a
        # 3 niveles; lo simple y seguro es prohibirlo.
        has_children = await db.execute(
            select(Category.id).where(Category.parent_id == category.id).limit(1)
        )
        if has_children.first() is not None:
            raise ValidationAppError(
                "Esta categoría tiene subcategorías; no puede pasar a ser subcategoría de otra.",
                code="CATEGORY_TOO_DEEP",
            )

    for field, value in updates.items():
        setattr(category, field, value)
    category.updated_at = datetime.now(UTC)
    await db.flush()
    await record_change(
        db,
        user_id=user_id,
        entity_type="category",
        op="upsert",
        entity=category,
        payload=CategoryOut.model_validate(category).model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(category)
    return category


async def archive_category(db: AsyncSession, user_id: UUID, category_id: UUID) -> None:
    category = await _get_owned(db, user_id, category_id)
    category.is_archived = True
    category.updated_at = datetime.now(UTC)
    await db.flush()
    await budget_service.retire_category_items(db, user_id, category.id)
    await record_change(
        db,
        user_id=user_id,
        entity_type="category",
        op="upsert",
        entity=category,
        payload=CategoryOut.model_validate(category).model_dump(mode="json"),
    )
    await db.commit()


async def merge_categories(
    db: AsyncSession, user_id: UUID, category_id: UUID, into_id: UUID
) -> None:
    if category_id == into_id:
        raise ValidationAppError(
            "No se puede fusionar una categoría consigo misma.", code="CATEGORY_MERGE_NOOP"
        )
    source = await _get_owned(db, user_id, category_id)
    target = await _get_owned(db, user_id, into_id)
    if source.kind != target.kind:
        raise ValidationAppError(
            "Solo se pueden fusionar categorías del mismo tipo (ingreso/gasto).",
            code="CATEGORY_KIND_MISMATCH",
        )

    await db.execute(
        update(Transaction)
        .where(Transaction.user_id == user_id, Transaction.category_id == source.id)
        .values(category_id=target.id)
    )
    source.is_archived = True
    source.updated_at = datetime.now(UTC)
    await db.flush()
    await budget_service.retire_category_items(db, user_id, source.id, merge_into=target.id)
    await record_change(
        db,
        user_id=user_id,
        entity_type="category",
        op="upsert",
        entity=source,
        payload=CategoryOut.model_validate(source).model_dump(mode="json"),
    )
    await db.commit()


async def reorder_categories(db: AsyncSession, user_id: UUID, ids: list[UUID]) -> None:
    for position, category_id in enumerate(ids):
        category = await _get_owned(db, user_id, category_id)
        category.sort_order = position
        category.updated_at = datetime.now(UTC)
    await db.commit()


async def seed_categories(db: AsyncSession, user_id: UUID) -> list[Category]:
    """Idempotente: no duplica si ya corrió antes (compara por nombre+kind)."""
    existing = await db.execute(
        select(Category.name, Category.kind).where(Category.user_id == user_id)
    )
    existing_keys = {(name, kind) for name, kind in existing.all()}

    created: list[Category] = []
    for parent_name, kind, children in _SEED:
        if (parent_name, kind) in existing_keys:
            continue
        parent = Category(id=uuid4(), user_id=user_id, name=parent_name, kind=kind)
        db.add(parent)
        await db.flush()
        await record_change(
            db,
            user_id=user_id,
            entity_type="category",
            op="upsert",
            entity=parent,
            payload=CategoryOut.model_validate(parent).model_dump(mode="json"),
        )
        created.append(parent)

        for child_name in children:
            if (child_name, kind) in existing_keys:
                continue
            child = Category(
                id=uuid4(), user_id=user_id, name=child_name, kind=kind, parent_id=parent.id
            )
            db.add(child)
            await db.flush()
            await record_change(
                db,
                user_id=user_id,
                entity_type="category",
                op="upsert",
                entity=child,
                payload=CategoryOut.model_validate(child).model_dump(mode="json"),
            )
            created.append(child)

    await db.commit()
    return created
