from domain.budget import (
    children_excess,
    effective_parents,
    rollup_spent,
    summarize_hierarchy,
)

FOOD, REST, GROCERIES, FUN = "food", "restaurants", "groceries", "fun"


def test_child_is_linked_only_when_parent_has_its_own_item() -> None:
    parents = effective_parents({FOOD: None, REST: FOOD, FUN: "leisure"})
    assert parents == {FOOD: None, REST: FOOD, FUN: None}  # "leisure" no está presupuestada


def test_rollup_sums_own_and_children_spend() -> None:
    spent: dict[str | None, int] = {FOOD: 100, REST: 300, GROCERIES: 50, FUN: 999}
    assert rollup_spent(FOOD, [REST, GROCERIES], spent) == 450
    assert rollup_spent(REST, [], spent) == 300
    assert rollup_spent("empty", [], spent) == 0


def test_children_excess_is_zero_when_they_fit() -> None:
    assert children_excess(2_000, [800, 1_000]) == 0
    assert children_excess(2_000, [800, 1_200]) == 0


def test_children_excess_reports_the_overflow() -> None:
    assert children_excess(2_000, [1_500, 1_000]) == 500


def test_summary_counts_only_roots_in_total() -> None:
    budgeted = {FOOD: 2_000, REST: 800, GROCERIES: 1_000, FUN: 500}
    summary = summarize_hierarchy(budgeted, {FOOD: None, REST: FOOD, GROCERIES: FOOD, FUN: None})
    assert summary.root_total_cents == 2_500  # FOOD + FUN, sin los hijos
    assert summary.children_budgeted == {FOOD: 1_800}
    assert summary.children_excess == {}


def test_summary_flags_parent_whose_children_overflow() -> None:
    budgeted = {FOOD: 1_000, REST: 800, GROCERIES: 500}
    summary = summarize_hierarchy(budgeted, {FOOD: None, REST: FOOD, GROCERIES: FOOD})
    assert summary.children_excess == {FOOD: 300}
    assert summary.root_total_cents == 1_000


def test_flat_budget_is_unchanged() -> None:
    budgeted = {FOOD: 2_000, FUN: 500}
    summary = summarize_hierarchy(budgeted, effective_parents({FOOD: None, FUN: None}))
    assert summary.root_total_cents == 2_500
    assert summary.children_budgeted == {}
