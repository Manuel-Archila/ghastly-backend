import pytest

from domain.categories import CategoryError, validate_category_depth


def test_allows_child_of_top_level_category() -> None:
    validate_category_depth(chosen_parent_has_parent=False)  # no debe lanzar


def test_rejects_third_level_category() -> None:
    with pytest.raises(CategoryError):
        validate_category_depth(chosen_parent_has_parent=True)
