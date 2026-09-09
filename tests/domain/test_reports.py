from datetime import date
from uuid import uuid4

import pytest

from domain.balances import AccountType
from domain.reports import (
    CategorySpend,
    PeriodTotals,
    UpcomingSource,
    build_cashflow_series,
    build_category_breakdown,
    build_category_comparison,
    build_upcoming_calendar,
    build_upcoming_preview,
    compare_totals,
    compute_income_averages,
    compute_net_worth,
    compute_savings_rate,
    debt_balance_as_of,
    generate_period_starts,
    select_top_categories,
    trailing_months,
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


def test_build_category_breakdown_computes_percent_of_total() -> None:
    rows = [
        CategorySpend(uuid4(), "Comida", 300_00),
        CategorySpend(uuid4(), "Transporte", 100_00),
    ]
    result = build_category_breakdown(rows)
    assert [item.percent_of_total for item in result] == [75, 25]


def test_build_category_breakdown_excludes_non_positive_amounts() -> None:
    rows = [
        CategorySpend(uuid4(), "Comida", 100_00),
        CategorySpend(uuid4(), "Reembolsado de más", -50_00),
        CategorySpend(uuid4(), "Sin movimiento", 0),
    ]
    result = build_category_breakdown(rows)
    assert [item.category_name for item in result] == ["Comida"]
    assert result[0].percent_of_total == 100


def test_build_category_breakdown_orders_by_amount_descending() -> None:
    rows = [
        CategorySpend(uuid4(), "Ropa", 100_00),
        CategorySpend(uuid4(), "Comida", 300_00),
    ]
    result = build_category_breakdown(rows)
    assert [item.category_name for item in result] == ["Comida", "Ropa"]


def test_build_category_breakdown_empty_rows_is_empty_list() -> None:
    assert build_category_breakdown([]) == []


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


def test_generate_period_starts_month_spans_full_range() -> None:
    starts = generate_period_starts(date(2026, 7, 15), date(2026, 9, 3), "month")
    assert starts == [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)]


def test_generate_period_starts_month_single_month() -> None:
    starts = generate_period_starts(date(2026, 9, 1), date(2026, 9, 30), "month")
    assert starts == [date(2026, 9, 1)]


def test_generate_period_starts_week_aligns_to_monday() -> None:
    # 2026-09-08 es martes; la semana que lo contiene arranca el lunes 2026-09-07.
    starts = generate_period_starts(date(2026, 9, 8), date(2026, 9, 8), "week")
    assert starts == [date(2026, 9, 7)]


def test_generate_period_starts_week_spans_multiple_weeks() -> None:
    starts = generate_period_starts(date(2026, 9, 1), date(2026, 9, 14), "week")
    assert starts == [date(2026, 8, 31), date(2026, 9, 7), date(2026, 9, 14)]


def test_build_cashflow_series_sums_income_and_expense_per_period() -> None:
    rows = [
        (date(2026, 7, 1), "income", 300_00),
        (date(2026, 7, 1), "expense", 100_00),
        (date(2026, 7, 1), "expense", 50_00),
        (date(2026, 8, 1), "income", 200_00),
    ]
    result = build_cashflow_series(rows, [date(2026, 7, 1), date(2026, 8, 1)])
    assert result[0].income_cents == 300_00
    assert result[0].expense_cents == 150_00
    assert result[0].net_cents == 150_00
    assert result[1].income_cents == 200_00
    assert result[1].expense_cents == 0
    assert result[1].net_cents == 200_00


def test_build_cashflow_series_fills_gaps_with_zero() -> None:
    rows = [(date(2026, 7, 1), "income", 100_00)]
    result = build_cashflow_series(rows, [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)])
    assert [p.period_start for p in result] == [
        date(2026, 7, 1),
        date(2026, 8, 1),
        date(2026, 9, 1),
    ]
    assert result[1].income_cents == 0
    assert result[1].expense_cents == 0
    assert result[1].net_cents == 0


def test_build_cashflow_series_empty_rows_still_returns_all_periods_at_zero() -> None:
    result = build_cashflow_series([], [date(2026, 7, 1), date(2026, 8, 1)])
    assert all(p.income_cents == 0 and p.expense_cents == 0 and p.net_cents == 0 for p in result)
    assert len(result) == 2


def test_build_upcoming_calendar_sorts_all_sources_without_capping() -> None:
    sources = [
        UpcomingSource("card_payment", uuid4(), "Tarjeta", date(2026, 9, 25), 500_00),
        UpcomingSource("debt_payment", uuid4(), "Préstamo", date(2026, 9, 5), 200_00),
        UpcomingSource("card_statement", uuid4(), "Tarjeta", date(2026, 9, 15), 0),
    ]
    result = build_upcoming_calendar(sources)
    assert [item.source_type for item in result] == [
        "debt_payment",
        "card_statement",
        "card_payment",
    ]


def test_build_upcoming_calendar_empty_sources_returns_empty_list() -> None:
    assert build_upcoming_calendar([]) == []


def test_debt_balance_as_of_subtracts_principal_paid_up_to_date() -> None:
    payments = [(date(2026, 2, 1), 100_00), (date(2026, 3, 1), 100_00)]
    assert debt_balance_as_of(1_000_00, date(2026, 1, 1), payments, date(2026, 2, 15)) == 900_00


def test_debt_balance_as_of_ignores_payments_after_the_date() -> None:
    payments = [(date(2026, 5, 1), 100_00)]
    assert debt_balance_as_of(1_000_00, date(2026, 1, 1), payments, date(2026, 2, 15)) == 1_000_00


def test_debt_balance_as_of_zero_before_debt_existed() -> None:
    assert debt_balance_as_of(1_000_00, date(2026, 6, 1), [], date(2026, 1, 1)) == 0


def test_debt_balance_as_of_never_goes_negative() -> None:
    payments = [(date(2026, 2, 1), 1_500_00)]
    assert debt_balance_as_of(1_000_00, date(2026, 1, 1), payments, date(2026, 3, 1)) == 0


def test_trailing_months_ascending_ending_at_end_month() -> None:
    assert trailing_months("2026-09", 3) == ["2026-07", "2026-08", "2026-09"]


def test_trailing_months_crosses_year_boundary() -> None:
    assert trailing_months("2026-01", 3) == ["2025-11", "2025-12", "2026-01"]


def test_trailing_months_single_month_is_just_end_month() -> None:
    assert trailing_months("2026-09", 1) == ["2026-09"]


def test_trailing_months_rejects_non_positive_months() -> None:
    with pytest.raises(ValueError):
        trailing_months("2026-09", 0)


def test_compare_totals_computes_percent_change() -> None:
    result = compare_totals(
        PeriodTotals(income_cents=300_00, expense_cents=100_00),
        PeriodTotals(income_cents=330_00, expense_cents=150_00),
    )
    assert result.income_change_percent == 10
    assert result.expense_change_percent == 50


def test_compare_totals_zero_base_with_movement_is_undefined() -> None:
    result = compare_totals(
        PeriodTotals(income_cents=0, expense_cents=0),
        PeriodTotals(income_cents=100_00, expense_cents=0),
    )
    assert result.income_change_percent is None
    assert result.expense_change_percent == 0


def test_build_category_comparison_includes_categories_from_either_month() -> None:
    comida_id, transporte_id, ropa_id = uuid4(), uuid4(), uuid4()
    a_rows = [
        CategorySpend(comida_id, "Comida", 100_00),
        CategorySpend(transporte_id, "Transporte", 50_00),
    ]
    b_rows = [CategorySpend(comida_id, "Comida", 150_00), CategorySpend(ropa_id, "Ropa", 80_00)]

    result = build_category_comparison(a_rows, b_rows)
    by_name = {item.category_name: item for item in result}

    assert by_name["Comida"].a_amount_cents == 100_00
    assert by_name["Comida"].b_amount_cents == 150_00
    assert by_name["Comida"].delta_cents == 50_00
    assert by_name["Transporte"].b_amount_cents == 0
    assert by_name["Transporte"].delta_cents == -50_00
    assert by_name["Ropa"].a_amount_cents == 0
    assert by_name["Ropa"].percent_change is None  # nueva categoría, base 0


def test_build_category_comparison_orders_by_absolute_delta_descending() -> None:
    cat_a, cat_b = uuid4(), uuid4()
    a_rows = [CategorySpend(cat_a, "A", 100_00), CategorySpend(cat_b, "B", 100_00)]
    b_rows = [CategorySpend(cat_a, "A", 110_00), CategorySpend(cat_b, "B", 400_00)]
    result = build_category_comparison(a_rows, b_rows)
    assert [item.category_name for item in result] == ["B", "A"]


def test_compute_income_averages_splits_extraordinary_from_recurring() -> None:
    rows = [(100_00, False), (100_00, False), (100_00, False), (500_00, True)]
    avg_with, avg_recurring = compute_income_averages(rows, months=3)
    assert avg_with == 266_66  # (300_00 + 500_00) // 3
    assert avg_recurring == 100_00


def test_compute_income_averages_no_income_is_zero() -> None:
    assert compute_income_averages([], months=3) == (0, 0)


def test_compute_savings_rate_positive_and_negative() -> None:
    assert compute_savings_rate(income_cents=1_000_00, expense_cents=700_00) == 30
    assert compute_savings_rate(income_cents=1_000_00, expense_cents=1_200_00) == -20


def test_compute_savings_rate_no_income_is_undefined() -> None:
    assert compute_savings_rate(income_cents=0, expense_cents=500_00) is None
