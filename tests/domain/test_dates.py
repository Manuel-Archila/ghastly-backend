from datetime import date

from domain.dates import add_months_clamped, clamp_day, next_day_of_month


def test_clamp_day_returns_exact_day_when_month_has_it() -> None:
    assert clamp_day(2026, 1, 15) == date(2026, 1, 15)


def test_clamp_day_clamps_to_last_day_of_short_month() -> None:
    assert clamp_day(2026, 2, 31) == date(2026, 2, 28)  # 2026 no es bisiesto


def test_clamp_day_leap_year_february() -> None:
    assert clamp_day(2028, 2, 29) == date(2028, 2, 29)
    assert clamp_day(2028, 2, 30) == date(2028, 2, 29)


def test_add_months_clamped_simple_case() -> None:
    assert add_months_clamped(date(2026, 1, 15), 1) == date(2026, 2, 15)


def test_add_months_clamped_end_of_month_case() -> None:
    # El caso que rompe implementaciones ingenuas: 31 de enero + 1 mes.
    assert add_months_clamped(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_add_months_clamped_crosses_year_boundary() -> None:
    assert add_months_clamped(date(2026, 11, 30), 2) == date(2027, 1, 30)


def test_add_months_clamped_negative_months() -> None:
    assert add_months_clamped(date(2026, 3, 31), -1) == date(2026, 2, 28)


def test_add_months_clamped_zero_is_identity() -> None:
    assert add_months_clamped(date(2026, 5, 20), 0) == date(2026, 5, 20)


def test_next_day_of_month_inclusive_returns_today_when_it_matches() -> None:
    assert next_day_of_month(date(2026, 9, 15), 15, inclusive=True) == date(2026, 9, 15)


def test_next_day_of_month_exclusive_skips_today_to_next_month() -> None:
    assert next_day_of_month(date(2026, 9, 15), 15, inclusive=False) == date(2026, 10, 15)


def test_next_day_of_month_future_day_this_month() -> None:
    assert next_day_of_month(date(2026, 9, 5), 20, inclusive=True) == date(2026, 9, 20)


def test_next_day_of_month_past_day_rolls_to_next_month() -> None:
    assert next_day_of_month(date(2026, 9, 20), 5, inclusive=True) == date(2026, 10, 5)


def test_next_day_of_month_clamps_short_month() -> None:
    assert next_day_of_month(date(2026, 2, 1), 31, inclusive=True) == date(2026, 2, 28)
