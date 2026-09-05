from decimal import Decimal

import pytest

from domain.money import Money, MoneyError, sum_money


def test_rejects_float_cents() -> None:
    with pytest.raises(MoneyError):
        Money(10.5)  # type: ignore[arg-type]


def test_rejects_bool_as_cents() -> None:
    with pytest.raises(MoneyError):
        Money(True)  # bool es subtipo de int para el checker; el rechazo es en runtime


def test_add_same_currency() -> None:
    assert Money(1000, "GTQ").add(Money(500, "GTQ")) == Money(1500, "GTQ")


def test_add_different_currency_raises() -> None:
    with pytest.raises(MoneyError):
        Money(1000, "GTQ").add(Money(500, "USD"))


def test_subtract() -> None:
    assert Money(1000).subtract(Money(300)) == Money(700)


def test_negate() -> None:
    assert Money(500).negate() == Money(-500)
    assert Money(-500).negate() == Money(500)


def test_is_zero_and_is_negative() -> None:
    assert Money(0).is_zero()
    assert not Money(1).is_zero()
    assert Money(-1).is_negative()
    assert not Money(1).is_negative()


def test_allocate_exact_split() -> None:
    shares = Money(900).allocate(3)
    assert shares == [Money(300), Money(300), Money(300)]


def test_allocate_remainder_goes_to_last_share() -> None:
    # 1000 / 3 = 333.33... -> las dos primeras 333, la última absorbe el residuo.
    shares = Money(1000).allocate(3)
    assert shares == [Money(333), Money(333), Money(334)]
    assert sum(s.cents for s in shares) == 1000


def test_allocate_installments_case_06() -> None:
    # Celular de Q10,000.00 en 12 cuotas — ninguna cuota puede perder un centavo
    # y la suma de las cuotas debe cuadrar exactamente con el total (caso 6).
    shares = Money(1_000_000).allocate(12)
    assert len(shares) == 12
    assert sum(s.cents for s in shares) == 1_000_000
    assert shares[0] == Money(83_333)
    assert shares[-1] == Money(83_337)  # 83_333*11 = 916_663; residuo 83_337


def test_allocate_rejects_non_positive_parts() -> None:
    with pytest.raises(MoneyError):
        Money(100).allocate(0)
    with pytest.raises(MoneyError):
        Money(100).allocate(-1)


def test_convert_freezes_rate_gtq_per_usd() -> None:
    # 100.00 USD a una tasa congelada de 7.85 GTQ por USD -> 785.00 GTQ
    usd = Money(10_000, "USD")
    gtq = usd.convert(Decimal("7.85"), "GTQ")
    assert gtq == Money(78_500, "GTQ")


def test_convert_rounds_half_up() -> None:
    # 1 centavo USD a 7.845 -> 7.845 centavos GTQ, half-up sube a 8.
    usd = Money(1, "USD")
    gtq = usd.convert(Decimal("7.845"), "GTQ")
    assert gtq == Money(8, "GTQ")
    # 33 centavos * 7.5 = 247.5 -> half-up sube a 248
    usd2 = Money(33, "USD")
    gtq2 = usd2.convert(Decimal("7.5"), "GTQ")
    assert gtq2 == Money(248, "GTQ")


def test_format_gtq() -> None:
    assert Money(123_456, "GTQ").format() == "Q1,234.56"
    assert Money(-500, "GTQ").format() == "-Q5.00"


def test_format_usd() -> None:
    assert Money(100, "USD").format() == "$1.00"


def test_sum_money() -> None:
    total = sum_money([Money(100), Money(200), Money(300)])
    assert total == Money(600)


def test_sum_money_empty_list() -> None:
    assert sum_money([]) == Money(0)
