import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from domain.duplicates import DuplicateCandidate, ExistingTransaction, find_possible_duplicate

ACCOUNT_A = uuid.uuid4()
ACCOUNT_B = uuid.uuid4()
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _existing(
    *,
    id: UUID | None = None,
    account_id: UUID = ACCOUNT_A,
    amount_cents: int = 5_000,
    occurred_at: datetime = NOW,
) -> ExistingTransaction:
    return ExistingTransaction(
        id=id or uuid.uuid4(),
        account_id=account_id,
        amount_cents=amount_cents,
        occurred_at=occurred_at,
    )


def test_same_account_amount_and_time_is_duplicate() -> None:
    candidate = DuplicateCandidate(ACCOUNT_A, 5_000, NOW + timedelta(minutes=2))
    match = find_possible_duplicate(candidate, [_existing()])
    assert match is not None


def test_different_account_is_not_duplicate() -> None:
    candidate = DuplicateCandidate(ACCOUNT_B, 5_000, NOW)
    assert find_possible_duplicate(candidate, [_existing()]) is None


def test_different_amount_is_not_duplicate() -> None:
    candidate = DuplicateCandidate(ACCOUNT_A, 5_001, NOW)
    assert find_possible_duplicate(candidate, [_existing()]) is None


def test_outside_window_is_not_duplicate() -> None:
    candidate = DuplicateCandidate(ACCOUNT_A, 5_000, NOW + timedelta(minutes=6))
    assert find_possible_duplicate(candidate, [_existing()], window_minutes=5) is None


def test_exactly_at_window_boundary_is_duplicate() -> None:
    candidate = DuplicateCandidate(ACCOUNT_A, 5_000, NOW + timedelta(minutes=5))
    assert find_possible_duplicate(candidate, [_existing()], window_minutes=5) is not None


def test_no_existing_transactions_is_not_duplicate() -> None:
    candidate = DuplicateCandidate(ACCOUNT_A, 5_000, NOW)
    assert find_possible_duplicate(candidate, []) is None
