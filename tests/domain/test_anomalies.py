from uuid import uuid4

from domain.anomalies import detect_anomalies
from domain.reports import CategorySpend


def test_detect_anomalies_flags_category_above_threshold_over_average() -> None:
    comida_id = uuid4()
    current = [CategorySpend(comida_id, "Comida", 700_00)]
    previous = [CategorySpend(comida_id, "Comida", 1_500_00)]  # promedio 500_00/mes
    result = detect_anomalies(current, previous, months=3)
    assert len(result) == 1
    assert result[0].current_cents == 700_00
    assert result[0].average_cents == 500_00
    assert result[0].percent_increase == 40


def test_detect_anomalies_ignores_increase_below_threshold() -> None:
    comida_id = uuid4()
    current = [CategorySpend(comida_id, "Comida", 550_00)]
    previous = [CategorySpend(comida_id, "Comida", 1_500_00)]  # promedio 500_00, +10%
    assert detect_anomalies(current, previous, months=3) == []


def test_detect_anomalies_ignores_tiny_baselines_even_with_big_percent() -> None:
    ropa_id = uuid4()
    current = [CategorySpend(ropa_id, "Ropa", 30_00)]
    previous = [CategorySpend(ropa_id, "Ropa", 30_00)]  # promedio 10_00 — bajo MIN_BASELINE_CENTS
    assert detect_anomalies(current, previous, months=3) == []


def test_detect_anomalies_ignores_category_spending_at_or_below_average() -> None:
    comida_id = uuid4()
    current = [CategorySpend(comida_id, "Comida", 500_00)]
    previous = [CategorySpend(comida_id, "Comida", 1_500_00)]  # igual al promedio
    assert detect_anomalies(current, previous, months=3) == []


def test_detect_anomalies_new_category_with_no_history_is_not_an_anomaly() -> None:
    nueva_id = uuid4()
    current = [CategorySpend(nueva_id, "Nueva", 200_00)]
    assert detect_anomalies(current, [], months=3) == []


def test_detect_anomalies_orders_by_percent_increase_descending() -> None:
    comida_id, transporte_id = uuid4(), uuid4()
    current = [
        CategorySpend(comida_id, "Comida", 800_00),  # +60%
        CategorySpend(transporte_id, "Transporte", 700_00),  # +40%
    ]
    previous = [
        CategorySpend(comida_id, "Comida", 1_500_00),  # promedio 500_00
        CategorySpend(transporte_id, "Transporte", 1_500_00),  # promedio 500_00
    ]
    result = detect_anomalies(current, previous, months=3)
    assert [item.category_name for item in result] == ["Comida", "Transporte"]


def test_detect_anomalies_empty_current_is_empty() -> None:
    assert detect_anomalies([], [], months=3) == []
