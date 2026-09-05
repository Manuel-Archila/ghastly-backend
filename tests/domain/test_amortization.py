from decimal import Decimal

import pytest

from domain.amortization import AmortizationError, amortize, fixed_payment_cents


def test_zero_rate_matches_money_allocate() -> None:
    entries = amortize(1_000_000, Decimal(0), 12)
    assert len(entries) == 12
    assert sum(e.principal_cents for e in entries) == 1_000_000
    assert all(e.interest_cents == 0 for e in entries)
    assert entries[0].payment_cents == 83_333
    assert entries[-1].payment_cents == 83_337
    assert entries[-1].remaining_balance_cents == 0


def test_zero_rate_balance_decreases_to_zero() -> None:
    entries = amortize(900, Decimal(0), 3)
    balances = [e.remaining_balance_cents for e in entries]
    assert balances == [600, 300, 0]


def test_with_interest_principal_sums_to_original() -> None:
    entries = amortize(120_000, Decimal("0.02"), 12)
    assert sum(e.principal_cents for e in entries) == 120_000
    assert entries[-1].remaining_balance_cents == 0


def test_with_interest_balance_strictly_decreases() -> None:
    entries = amortize(120_000, Decimal("0.02"), 12)
    balances = [e.remaining_balance_cents for e in entries]
    assert balances == sorted(balances, reverse=True)
    assert balances[-1] == 0


def test_with_interest_first_payment_interest_matches_full_balance() -> None:
    entries = amortize(120_000, Decimal("0.02"), 12)
    # interés del primer pago = 2% de 120,000 = 2,400
    assert entries[0].interest_cents == 2_400


def test_with_interest_payment_equals_principal_plus_interest() -> None:
    entries = amortize(120_000, Decimal("0.02"), 12)
    for entry in entries:
        assert entry.payment_cents == entry.principal_cents + entry.interest_cents


def test_rejects_non_positive_principal() -> None:
    with pytest.raises(AmortizationError):
        amortize(0, Decimal("0.02"), 12)


def test_rejects_non_positive_num_payments() -> None:
    with pytest.raises(AmortizationError):
        amortize(1000, Decimal(0), 0)


def test_fixed_payment_zero_rate_is_ceiling_division() -> None:
    assert fixed_payment_cents(1000, Decimal(0), 3) == 334
