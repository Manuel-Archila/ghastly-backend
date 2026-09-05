from datetime import date

from domain.dates import add_months_clamped, clamp_day


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
