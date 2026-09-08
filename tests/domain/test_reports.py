from datetime import date
from uuid import uuid4

from domain.balances import AccountType
from domain.reports import (
    CategorySpend,
    UpcomingSource,
    build_upcoming_preview,
    compute_net_worth,
    select_top_categories,
)


def test_net_worth_sums_assets_and_subtracts_liability_account_balances() -> None:
    balances: list[tuple[AccountType, int]] = [("checking", 500_000), ("credit_card", 100_000)]
    assert compute_net_worth(balances, unlinked_active_debt_total_cents=0) == 400_000


def test_net_worth_treats_credit_card_and_loan_accounts_as_liabilities() -> None:
    balances: list[tuple[AccountType, int]] = [
        ("savings", 200_000),
        ("credit_card", 50_000),
        ("loan", 300_000),
    ]
    assert compute_net_worth(balances, unlinked_active_debt_total_cents=0) == -150_000


def test_net_worth_subtracts_unlinked_active_debt_balances() -> None:
    balances: list[tuple[AccountType, int]] = [("checking", 1_000_000)]
    assert compute_net_worth(balances, unlinked_active_debt_total_cents=250_000) == 750_000


def test_net_worth_empty_accounts_and_debts_is_zero() -> None:
    assert compute_net_worth([], unlinked_active_debt_total_cents=0) == 0


def test_select_top_categories_orders_by_net_spend_descending() -> None:
    rows = [
        CategorySpend(uuid4(), "Ropa", 100_00),
        CategorySpend(uuid4(), "Comida", 300_00),
        CategorySpend(uuid4(), "Transporte", 200_00),
    ]
    result = select_top_categories(rows)
    assert [row.category_name for row in result] == ["Comida", "Transporte", "Ropa"]


def test_select_top_categories_excludes_non_positive_net_spend() -> None:
    rows = [
        CategorySpend(uuid4(), "Comida", 100_00),
        CategorySpend(uuid4(), "Reembolsado de más", -50_00),
        CategorySpend(uuid4(), "Sin movimiento", 0),
    ]
    result = select_top_categories(rows)
    assert [row.category_name for row in result] == ["Comida"]


def test_select_top_categories_ties_broken_by_name_ascending() -> None:
    rows = [
        CategorySpend(uuid4(), "Zapatos", 100_00),
        CategorySpend(uuid4(), "Alimentación", 100_00),
    ]
    result = select_top_categories(rows)
    assert [row.category_name for row in result] == ["Alimentación", "Zapatos"]


def test_select_top_categories_caps_at_limit() -> None:
    rows = [CategorySpend(uuid4(), f"Cat {i}", (6 - i) * 100) for i in range(6)]
    result = select_top_categories(rows, limit=5)
    assert len(result) == 5


def test_build_upcoming_preview_merges_installments_and_recurring_sorted_by_date() -> None:
    sources = [
        UpcomingSource("recurring", uuid4(), "Netflix", date(2026, 9, 20), 100_00),
        UpcomingSource("installment", uuid4(), "Laptop 3/12", date(2026, 9, 10), 250_00),
    ]
    result = build_upcoming_preview(sources)
    assert [item.name for item in result] == ["Laptop 3/12", "Netflix"]


def test_build_upcoming_preview_caps_at_limit() -> None:
    sources = [
        UpcomingSource("recurring", uuid4(), f"Item {i}", date(2026, 9, 1 + i), 100_00)
        for i in range(7)
    ]
    result = build_upcoming_preview(sources, limit=5)
    assert len(result) == 5


def test_build_upcoming_preview_empty_sources_returns_empty_list() -> None:
    assert build_upcoming_preview([]) == []
