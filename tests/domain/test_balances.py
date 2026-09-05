import pytest

from domain.balances import (
    BalanceError,
    LedgerEntry,
    compute_balance,
    resolve_adjustment,
    signed_delta,
)


def test_expense_reduces_asset_account() -> None:
    entry = LedgerEntry(kind="expense", amount_cents=1000)
    assert signed_delta(entry, "checking") == -1000


def test_income_increases_asset_account() -> None:
    entry = LedgerEntry(kind="income", amount_cents=1000)
    assert signed_delta(entry, "savings") == 1000


def test_expense_increases_credit_card_debt() -> None:
    # Comprar con tarjeta aumenta lo que se debe.
    entry = LedgerEntry(kind="expense", amount_cents=500)
    assert signed_delta(entry, "credit_card") == 500


def test_income_reduces_credit_card_debt() -> None:
    # Un reembolso acreditado directo a la tarjeta reduce la deuda.
    entry = LedgerEntry(kind="income", amount_cents=200)
    assert signed_delta(entry, "credit_card") == -200


def test_transfer_out_reduces_asset_account() -> None:
    entry = LedgerEntry(kind="transfer", amount_cents=300, transfer_direction="out")
    assert signed_delta(entry, "checking") == -300


def test_transfer_in_increases_asset_account() -> None:
    entry = LedgerEntry(kind="transfer", amount_cents=300, transfer_direction="in")
    assert signed_delta(entry, "cash") == 300


def test_transfer_in_reduces_credit_card_debt() -> None:
    # Pagar la tarjeta: la plata ENTRA a la tarjeta y reduce lo que se debe.
    entry = LedgerEntry(kind="transfer", amount_cents=1000, transfer_direction="in")
    assert signed_delta(entry, "credit_card") == -1000


def test_transfer_out_increases_credit_card_debt() -> None:
    # Caso raro (p. ej. un avance de efectivo) pero simétrico.
    entry = LedgerEntry(kind="transfer", amount_cents=100, transfer_direction="out")
    assert signed_delta(entry, "credit_card") == 100


def test_transfer_without_direction_raises() -> None:
    entry = LedgerEntry(kind="transfer", amount_cents=100)
    with pytest.raises(BalanceError):
        signed_delta(entry, "checking")


def test_negative_amount_raises() -> None:
    entry = LedgerEntry(kind="expense", amount_cents=-100)
    with pytest.raises(BalanceError):
        signed_delta(entry, "checking")


def test_unknown_kind_raises() -> None:
    entry = LedgerEntry(kind="bogus", amount_cents=100)  # type: ignore[arg-type]
    with pytest.raises(BalanceError):
        signed_delta(entry, "checking")


def test_compute_balance_checking_account() -> None:
    entries = [
        LedgerEntry(kind="income", amount_cents=500_000),  # salario
        LedgerEntry(kind="expense", amount_cents=100_000),  # renta
        LedgerEntry(kind="transfer", amount_cents=50_000, transfer_direction="out"),  # a ahorro
    ]
    balance = compute_balance(0, entries, "checking")
    assert balance == 350_000


def test_compute_balance_credit_card_purchase_then_payment() -> None:
    entries = [
        LedgerEntry(kind="expense", amount_cents=200_000),  # compra: sube la deuda
        LedgerEntry(kind="transfer", amount_cents=150_000, transfer_direction="in"),  # pago
    ]
    balance = compute_balance(0, entries, "credit_card")
    assert balance == 50_000  # queda debiendo Q500.00


def test_compute_balance_starts_from_initial_balance() -> None:
    entries = [LedgerEntry(kind="expense", amount_cents=100)]
    assert compute_balance(10_000, entries, "checking") == 9_900


def test_resolve_adjustment_asset_account_real_balance_higher() -> None:
    # El saldo real (10,000) es mayor que el que tenía la app (8,000): ingreso.
    kind, amount = resolve_adjustment(2_000, "checking")
    assert kind == "income"
    assert amount == 2_000


def test_resolve_adjustment_asset_account_real_balance_lower() -> None:
    kind, amount = resolve_adjustment(-500, "checking")
    assert kind == "expense"
    assert amount == 500


def test_resolve_adjustment_credit_card_owes_more_than_tracked() -> None:
    # Debo más de lo que la app cree: sube la deuda -> es como un "gasto".
    kind, amount = resolve_adjustment(1_500, "credit_card")
    assert kind == "expense"
    assert amount == 1_500


def test_resolve_adjustment_credit_card_owes_less_than_tracked() -> None:
    kind, amount = resolve_adjustment(-300, "credit_card")
    assert kind == "income"
    assert amount == 300


def test_resolve_adjustment_zero_diff_raises() -> None:
    with pytest.raises(BalanceError):
        resolve_adjustment(0, "checking")
