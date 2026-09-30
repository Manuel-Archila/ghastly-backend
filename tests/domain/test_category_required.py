"""Un gasto o un ingreso no pueden existir sin categoría (las transferencias sí)."""

from uuid import uuid4

import pytest

from domain.category_rule import is_missing_required_category, requires_category


@pytest.mark.parametrize("kind", ["expense", "income"])
def test_expenses_and_income_require_a_category(kind: str) -> None:
    assert requires_category(kind) is True


def test_transfers_do_not_require_a_category() -> None:
    assert requires_category("transfer") is False


@pytest.mark.parametrize("kind", ["expense", "income"])
def test_expense_or_income_without_category_is_missing_it(kind: str) -> None:
    assert is_missing_required_category(kind, None) is True


@pytest.mark.parametrize("kind", ["expense", "income"])
def test_expense_or_income_with_category_is_fine(kind: str) -> None:
    assert is_missing_required_category(kind, uuid4()) is False


def test_a_transfer_without_category_is_fine() -> None:
    assert is_missing_required_category("transfer", None) is False
