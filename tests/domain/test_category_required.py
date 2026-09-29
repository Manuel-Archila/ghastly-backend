"""Un gasto no puede existir sin categoría (los ingresos y transferencias sí)."""

from uuid import uuid4

import pytest

from domain.category_rule import is_missing_required_category, requires_category


def test_an_expense_requires_a_category() -> None:
    assert requires_category("expense") is True


@pytest.mark.parametrize("kind", ["income", "transfer"])
def test_income_and_transfers_do_not_require_a_category(kind: str) -> None:
    assert requires_category(kind) is False


def test_an_expense_without_category_is_missing_it() -> None:
    assert is_missing_required_category("expense", None) is True


def test_an_expense_with_category_is_fine() -> None:
    assert is_missing_required_category("expense", uuid4()) is False


@pytest.mark.parametrize("kind", ["income", "transfer"])
def test_income_and_transfers_without_category_are_fine(kind: str) -> None:
    assert is_missing_required_category(kind, None) is False
