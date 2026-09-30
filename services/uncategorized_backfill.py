"""Asigna "Sin categoría" a los gastos que ya existían sin categoría.

Se corre UNA vez (y se puede repetir: es idempotente) para que los datos
anteriores cumplan la regla "un gasto no puede existir sin categoría"
(`domain/category_rule.py`). Cubre lo que termina siendo un gasto: transacciones,
reglas recurrentes, planes de cuotas y plantillas.

Escribe directo en las filas y NO pasa por los servicios de edición a propósito:
`update_transaction` dispara alertas de presupuesto (y de ahí, notificaciones
push) por cada gasto tocado, y aquí no se quiere avisar de nada.

Cada fila actualizada se registra en `change_log` (`record_change`), que es lo
que hace que los teléfonos se enteren en su próximo pull; sin eso el cambio
existiría solo en el servidor. Las filas ya borradas se corrigen pero no se
registran: los clientes ya las tienen como borradas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, union
from sqlalchemy.ext.asyncio import AsyncSession

from schemas.installments import InstallmentPlanOut
from schemas.recurring import RecurringRuleOut
from schemas.transaction_templates import TransactionTemplateOut
from schemas.transactions import TransactionOut
from services.change_log import record_change
from services.reserved_categories import get_or_create_uncategorized_category
from storage.models.category import Category
from storage.models.installment import InstallmentPlan
from storage.models.recurring import RecurringRule
from storage.models.transaction import Transaction
from storage.models.transaction_template import TransactionTemplate


@dataclass
class BackfillReport:
    users: int = 0
    transactions: int = 0
    recurring_rules: int = 0
    installment_plans: int = 0
    templates: int = 0
    categories_created: int = 0

    @property
    def total_rows(self) -> int:
        return self.transactions + self.recurring_rules + self.installment_plans + self.templates


# (modelo, entity_type del change_log, schema de salida, ¿solo gastos?)
_TARGETS: list[tuple[Any, str, Any, bool]] = [
    (Transaction, "transaction", TransactionOut, True),
    (RecurringRule, "recurring_rule", RecurringRuleOut, True),
    (InstallmentPlan, "installment_plan", InstallmentPlanOut, False),  # siempre es gasto
    (TransactionTemplate, "transaction_template", TransactionTemplateOut, True),
]


def _uncategorized(model: Any, only_expenses: bool) -> Any:
    conditions = [model.category_id.is_(None)]
    if only_expenses:
        conditions.append(model.kind == "expense")
    return conditions


async def _users_with_gaps(db: AsyncSession) -> list[UUID]:
    selects = [
        select(model.user_id).where(*_uncategorized(model, only_expenses))
        for model, _et, _out, only_expenses in _TARGETS
    ]
    rows = await db.execute(union(*selects))
    return sorted(row[0] for row in rows)


async def backfill_uncategorized(db: AsyncSession, *, apply: bool) -> BackfillReport:
    """Con `apply=False` solo cuenta lo que haría, sin escribir nada."""
    report = BackfillReport()
    for user_id in await _users_with_gaps(db):
        report.users += 1

        existing = await db.execute(
            select(Category.id).where(
                Category.user_id == user_id,
                Category.name == "Sin categoría",
                Category.kind == "expense",
                Category.deleted_at.is_(None),
            )
        )
        needs_category = existing.first() is None

        category: Category | None = None
        if apply:
            category = await get_or_create_uncategorized_category(db, user_id)
        if needs_category:
            report.categories_created += 1

        for model, entity_type, out_schema, only_expenses in _TARGETS:
            result = await db.execute(
                select(model).where(model.user_id == user_id, *_uncategorized(model, only_expenses))
            )
            rows = list(result.scalars())
            counter = {
                "transaction": "transactions",
                "recurring_rule": "recurring_rules",
                "installment_plan": "installment_plans",
                "transaction_template": "templates",
            }[entity_type]
            setattr(report, counter, getattr(report, counter) + len(rows))
            if not apply:
                continue

            assert category is not None
            now = datetime.now(UTC)
            for row in rows:
                row.category_id = category.id
                row.updated_at = now
                await db.flush()
                if row.deleted_at is None:
                    await record_change(
                        db,
                        user_id=user_id,
                        entity_type=entity_type,
                        op="upsert",
                        entity=row,
                        payload=out_schema.model_validate(row).model_dump(mode="json"),
                    )
        if apply:
            await db.commit()
    return report
