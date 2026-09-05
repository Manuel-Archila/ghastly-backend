from datetime import date
from decimal import Decimal

from domain.installments import generate_installment_schedule


def test_case_06_installments_do_not_hit_budget_and_never_lose_a_cent() -> None:
    # Celular de Q10,000.00 en 12 cuotas sin intereses.
    schedule = generate_installment_schedule(1_000_000, 12, date(2026, 9, 15))
    assert len(schedule) == 12
    assert sum(e.amount_cents for e in schedule) == 1_000_000
    assert schedule[0].number == 1
    assert schedule[0].due_date == date(2026, 9, 15)
    assert schedule[-1].number == 12
    assert all(e.interest_cents == 0 for e in schedule)


def test_due_dates_advance_one_month_at_a_time() -> None:
    schedule = generate_installment_schedule(300, 3, date(2026, 1, 31))
    due_dates = [e.due_date for e in schedule]
    # 31 de enero -> 28 de febrero (no existe el 31) -> 31 de marzo.
    assert due_dates == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]


def test_with_interest_splits_principal_and_interest() -> None:
    schedule = generate_installment_schedule(
        120_000, 12, date(2026, 1, 1), monthly_interest_rate=Decimal("0.02")
    )
    assert sum(e.principal_cents for e in schedule) == 120_000
    assert schedule[0].interest_cents == 2_400
    assert schedule[0].amount_cents == schedule[0].principal_cents + schedule[0].interest_cents
