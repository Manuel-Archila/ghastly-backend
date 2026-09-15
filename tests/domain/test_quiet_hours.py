from datetime import time

from domain.quiet_hours import is_within_quiet_hours


def test_no_quiet_hours_configured_is_never_within() -> None:
    assert is_within_quiet_hours(time(23, 0), None, None) is False


def test_same_day_window_inside() -> None:
    assert is_within_quiet_hours(time(13, 0), time(12, 0), time(14, 0)) is True


def test_same_day_window_outside() -> None:
    assert is_within_quiet_hours(time(15, 0), time(12, 0), time(14, 0)) is False


def test_same_day_window_boundaries_are_half_open() -> None:
    assert is_within_quiet_hours(time(12, 0), time(12, 0), time(14, 0)) is True
    assert is_within_quiet_hours(time(14, 0), time(12, 0), time(14, 0)) is False


def test_overnight_window_wraps_midnight() -> None:
    # 22:00-07:00: dentro a las 23:00 y a las 06:00, fuera a las 12:00.
    assert is_within_quiet_hours(time(23, 0), time(22, 0), time(7, 0)) is True
    assert is_within_quiet_hours(time(6, 0), time(22, 0), time(7, 0)) is True
    assert is_within_quiet_hours(time(12, 0), time(22, 0), time(7, 0)) is False


def test_equal_start_and_end_is_never_within() -> None:
    assert is_within_quiet_hours(time(10, 0), time(9, 0), time(9, 0)) is False
