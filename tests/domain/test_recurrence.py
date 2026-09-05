from datetime import date

import pytest

from domain.recurrence import (
    RecurrenceError,
    detect_price_increase,
    expand_occurrences,
    monthly_equivalent_cents,
    next_occurrence,
)


def test_daily() -> None:
    assert next_occurrence(date(2026, 9, 4), "daily") == date(2026, 9, 5)


def test_weekly_with_interval() -> None:
    assert next_occurrence(date(2026, 9, 4), "weekly", interval=2) == date(2026, 9, 18)


def test_monthly() -> None:
    assert next_occurrence(date(2026, 9, 4), "monthly") == date(2026, 10, 4)


def test_monthly_end_of_month_case() -> None:
    # El caso obligatorio: día 31 en febrero.
    assert next_occurrence(date(2026, 1, 31), "monthly") == date(2026, 2, 28)


def test_quarterly() -> None:
    assert next_occurrence(date(2026, 1, 31), "quarterly") == date(2026, 4, 30)


def test_yearly() -> None:
    # Aguinaldo/Bono 14 (caso 12): se repite el mismo día cada año.
    assert next_occurrence(date(2026, 12, 15), "yearly") == date(2027, 12, 15)


def test_rejects_non_positive_interval() -> None:
    with pytest.raises(RecurrenceError):
        next_occurrence(date(2026, 1, 1), "monthly", interval=0)


def test_rejects_unknown_frequency() -> None:
    with pytest.raises(RecurrenceError):
        next_occurrence(date(2026, 1, 1), "bogus")  # type: ignore[arg-type]


def test_expand_occurrences_within_range() -> None:
    occurrences = expand_occurrences(date(2026, 1, 1), "monthly", 1, date(2026, 4, 1))
    assert occurrences == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)]


def test_expand_occurrences_start_after_until_is_empty() -> None:
    assert expand_occurrences(date(2026, 5, 1), "monthly", 1, date(2026, 1, 1)) == []


def test_detect_price_increase() -> None:
    assert detect_price_increase(7_900, 8_900) is True
    assert detect_price_increase(8_900, 8_900) is False
    assert detect_price_increase(8_900, 7_900) is False


def test_monthly_equivalent_for_monthly_is_identity() -> None:
    assert monthly_equivalent_cents(8_900, "monthly", 1) == 8_900


def test_monthly_equivalent_for_yearly_divides_by_twelve() -> None:
    # Bono 14 / seguro anual de Q12,000 -> Q1,000/mes.
    assert monthly_equivalent_cents(1_200_000, "yearly", 1) == 100_000


def test_monthly_equivalent_for_weekly_uses_average_weeks_per_month() -> None:
    assert monthly_equivalent_cents(100, "weekly", 1) == 435  # 100 * 4.345


def test_monthly_equivalent_respects_interval() -> None:
    # Cada 2 semanas -> la mitad de ocurrencias por mes que semanal simple.
    assert monthly_equivalent_cents(100, "weekly", 2) == 217


def test_monthly_equivalent_rejects_non_positive_interval() -> None:
    with pytest.raises(RecurrenceError):
        monthly_equivalent_cents(100, "monthly", 0)
