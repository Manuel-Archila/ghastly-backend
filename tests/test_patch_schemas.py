"""Guarda de los schemas PATCH: un `null` explícito en un campo NOT NULL
tiene que ser un 422, no un IntegrityError -> 500 (ni, vía /sync/push, un
lote tumbado). Corre contra los modelos reales para que un campo nuevo no
se quede sin declarar en `non_nullable`."""

import pytest
from pydantic import ValidationError

from schemas.accounts import AccountUpdate
from schemas.budgets import BudgetItemUpdate, BudgetUpdate
from schemas.categories import CategoryUpdate
from schemas.common import PatchModel
from schemas.debts import DebtUpdate
from schemas.goals import GoalUpdate
from schemas.installments import InstallmentPlanUpdate
from schemas.notifications import NotificationPreferencesUpdate
from schemas.recurring import RecurringRuleUpdate
from schemas.transactions import TransactionUpdate
from storage.models.account import Account
from storage.models.budget import Budget, BudgetItem
from storage.models.category import Category
from storage.models.debt import Debt
from storage.models.goal import Goal
from storage.models.installment import InstallmentPlan
from storage.models.notification import NotificationPreferences
from storage.models.recurring import RecurringRule
from storage.models.transaction import Transaction

# (schema, modelo). `MeUpdateRequest` no entra: su service ya ignora los null.
PATCH_SCHEMAS: list[tuple[type[PatchModel], type]] = [
    (AccountUpdate, Account),
    (BudgetItemUpdate, BudgetItem),
    (BudgetUpdate, Budget),
    (CategoryUpdate, Category),
    (DebtUpdate, Debt),
    (GoalUpdate, Goal),
    (InstallmentPlanUpdate, InstallmentPlan),
    (NotificationPreferencesUpdate, NotificationPreferences),
    (RecurringRuleUpdate, RecurringRule),
    (TransactionUpdate, Transaction),
]


@pytest.mark.parametrize(("schema", "model"), PATCH_SCHEMAS, ids=lambda x: x.__name__)
def test_non_nullable_matches_the_not_null_columns(schema: type[PatchModel], model: type) -> None:
    columns = model.__table__.columns  # type: ignore[attr-defined]
    expected = {name for name in schema.model_fields if not columns[name].nullable}
    assert schema.non_nullable == expected


@pytest.mark.parametrize(("schema", "model"), PATCH_SCHEMAS, ids=lambda x: x.__name__)
def test_explicit_null_is_rejected_only_on_non_nullable_fields(
    schema: type[PatchModel], model: type
) -> None:
    for field in schema.model_fields:
        if field in schema.non_nullable:
            with pytest.raises(ValidationError) as exc_info:
                schema.model_validate({field: None})
            assert exc_info.value.errors()[0]["loc"] == (field,)
        else:
            assert schema.model_validate({field: None}).model_fields_set == {field}


@pytest.mark.parametrize(("schema", "model"), PATCH_SCHEMAS, ids=lambda x: x.__name__)
def test_omitted_fields_are_not_validated(schema: type[PatchModel], model: type) -> None:
    assert schema.model_validate({}).model_fields_set == set()
