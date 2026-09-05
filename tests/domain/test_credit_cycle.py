from datetime import date

from domain.credit_cycle import compute_current_cycle


def test_statement_later_this_month() -> None:
    # Hoy 4 de sept, corte el 18: el corte todavía no pasó este mes.
    cycle = compute_current_cycle(date(2026, 9, 4), statement_day=18, payment_due_day=3)
    assert cycle.statement_date == date(2026, 9, 18)
    assert cycle.days_until_statement == 14


def test_statement_today_counts_as_this_cycle() -> None:
    cycle = compute_current_cycle(date(2026, 9, 18), statement_day=18, payment_due_day=3)
    assert cycle.statement_date == date(2026, 9, 18)
    assert cycle.days_until_statement == 0


def test_statement_already_passed_rolls_to_next_month() -> None:
    # Hoy 20 de sept, corte el 18: ya pasó, el próximo es en octubre.
    cycle = compute_current_cycle(date(2026, 9, 20), statement_day=18, payment_due_day=3)
    assert cycle.statement_date == date(2026, 10, 18)


def test_payment_due_after_statement_same_month() -> None:
    # Corte el 5, pago el 20: el pago cae en el mismo mes que el corte.
    cycle = compute_current_cycle(date(2026, 9, 1), statement_day=5, payment_due_day=20)
    assert cycle.statement_date == date(2026, 9, 5)
    assert cycle.payment_due_date == date(2026, 9, 20)


def test_payment_due_before_statement_day_rolls_to_next_month() -> None:
    # Corte el 18, pago el 3: el pago (día 3) ya pasó relativo al corte
    # (día 18) dentro del mismo mes, así que cae en el mes siguiente.
    cycle = compute_current_cycle(date(2026, 9, 4), statement_day=18, payment_due_day=3)
    assert cycle.statement_date == date(2026, 9, 18)
    assert cycle.payment_due_date == date(2026, 10, 3)


def test_statement_day_clamped_in_short_month() -> None:
    cycle = compute_current_cycle(date(2026, 2, 1), statement_day=31, payment_due_day=15)
    assert cycle.statement_date == date(2026, 2, 28)
