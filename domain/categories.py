"""Jerarquía de categorías: dos niveles, nunca tres (PLAN-backend §5)."""

from __future__ import annotations


class CategoryError(ValueError):
    pass


def validate_category_depth(*, chosen_parent_has_parent: bool) -> None:
    """`chosen_parent_has_parent` es si la categoría elegida como padre YA es,
    a su vez, una subcategoría (tiene su propio `parent_id`). Si es así,
    colgarle un hijo crearía un tercer nivel — prohibido."""
    if chosen_parent_has_parent:
        raise CategoryError("una subcategoría no puede tener sub-subcategorías")
