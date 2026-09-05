import pytest

from domain.budget import (
    compute_item_progress,
    compute_rollover_out,
    expected_income,
    project_period_end,
    suggested_daily_pace,
)


def test_progress_basic_case() -> None:
    # Q21.40 de Q25.00 -> 86%
    progress = compute_item_progress(budgeted_cents=2_500, spent_cents=2_140)
    assert progress.available_cents == 360
    assert progress.percent_consumed == 86


def test_progress_overspent_shows_negative_available() -> None:
    progress = compute_item_progress(budgeted_cents=1_200, spent_cents=1_280)
    assert progress.available_cents == -80
    assert progress.percent_consumed == 107


def test_progress_includes_rollover_in_denominator() -> None:
    # Wireframe: Q340 / Q800 (+120) -> 37% (340 / 920)
    progress = compute_item_progress(budgeted_cents=800, spent_cents=340, rollover_in_cents=120)
    assert progress.percent_consumed == 37
    assert progress.available_cents == 580


def test_progress_zero_budget_and_zero_spent_is_zero_percent() -> None:
    progress = compute_item_progress(budgeted_cents=0, spent_cents=0)
    assert progress.percent_consumed == 0


def test_progress_zero_budget_with_spend_is_full_percent() -> None:
    progress = compute_item_progress(budgeted_cents=0, spent_cents=500)
    assert progress.percent_consumed == 100
    assert progress.available_cents == -500


def test_rollover_out_carries_surplus() -> None:
    assert compute_rollover_out(580, rollover_enabled=True) == 580


def test_rollover_out_disabled_carries_nothing() -> None:
    assert compute_rollover_out(580, rollover_enabled=False) == 0


def test_rollover_out_overspent_carries_nothing() -> None:
    # Un sobregiro no se convierte en deuda del mes siguiente.
    assert compute_rollover_out(-200, rollover_enabled=True) == 0


def test_project_period_end_extrapolates_daily_rate() -> None:
    # Q2,140 en 18 días de un mes de 30 -> ritmo Q118.89/día * 30 = Q3,566.67 -> 356667
    projected = project_period_end(spent_cents=214_000, days_elapsed=18, days_in_period=30)
    assert projected == 356_667


def test_project_period_end_zero_days_elapsed_returns_spent_as_is() -> None:
    assert project_period_end(spent_cents=0, days_elapsed=0, days_in_period=30) == 0
    assert project_period_end(spent_cents=500, days_elapsed=0, days_in_period=30) == 500


def test_suggested_daily_pace_splits_available_over_remaining_days() -> None:
    assert suggested_daily_pace(360, 12) == 30


def test_suggested_daily_pace_no_days_remaining_returns_all_available() -> None:
    assert suggested_daily_pace(360, 0) == 360


def test_expected_income_fixed() -> None:
    result = expected_income(
        "fixed", fixed_cents=500_000, previous_month_cents=480_000, avg_3m_cents=470_000
    )
    assert result == 500_000


def test_expected_income_previous_month() -> None:
    result = expected_income(
        "previous_month", fixed_cents=500_000, previous_month_cents=480_000, avg_3m_cents=470_000
    )
    assert result == 480_000


def test_expected_income_avg_3m() -> None:
    result = expected_income(
        "avg_3m", fixed_cents=500_000, previous_month_cents=480_000, avg_3m_cents=470_000
    )
    assert result == 470_000


def test_expected_income_unknown_basis_raises() -> None:
    with pytest.raises(ValueError):
        expected_income(
            "bogus",  # type: ignore[arg-type]
            fixed_cents=0,
            previous_month_cents=0,
            avg_3m_cents=0,
        )
