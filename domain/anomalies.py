"""Detección de anomalías de gasto por categoría, para GET /reports/anomalies
("gastaste 40% más en X", PLAN-backend.md §9). Compara el gasto neto del mes
contra el promedio de los 3 meses anteriores — mismo criterio que usará más
adelante el job `detect_anomalies` (Fase 4, todavía sin programar en
`jobs/scheduler.py`).

El service junta las sumas desde la DB (mismo query neteado de reembolsos
que `_category_rows` en `services/report_service.py`) y se las pasa a este
módulo — nada de esto toca SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from domain.reports import CategorySpend

# Bajo esta base, cualquier variación porcentual es ruido (una categoría que
# pasa de Q10 a Q30 "aumentó 200%" pero no es una anomalía real).
MIN_BASELINE_CENTS = 5_000  # Q50.00
# Gastar menos de un 30% de más contra el promedio no amerita avisar.
MIN_PERCENT_INCREASE = 30


@dataclass(frozen=True, slots=True)
class CategoryAnomaly:
    category_id: UUID
    category_name: str
    current_cents: int
    average_cents: int
    percent_increase: int


def detect_anomalies(
    current: list[CategorySpend],
    previous_months_total: list[CategorySpend],
    months: int,
) -> list[CategoryAnomaly]:
    """`previous_months_total` es la SUMA (no el promedio) del gasto neto de
    cada categoría en los `months` meses anteriores al que se evalúa — se
    promedia acá para no repetir la división en cada llamador. Ordenado por
    mayor % de aumento."""
    if months <= 0:
        raise ValueError("months debe ser positivo")

    average_by_id = {
        row.category_id: row.net_spent_cents // months for row in previous_months_total
    }

    anomalies = []
    for row in current:
        average_cents = average_by_id.get(row.category_id, 0)
        if average_cents < MIN_BASELINE_CENTS or row.net_spent_cents <= average_cents:
            continue
        percent_increase = int(
            (Decimal(row.net_spent_cents - average_cents) / Decimal(average_cents) * 100).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        if percent_increase < MIN_PERCENT_INCREASE:
            continue
        anomalies.append(
            CategoryAnomaly(
                category_id=row.category_id,
                category_name=row.category_name,
                current_cents=row.net_spent_cents,
                average_cents=average_cents,
                percent_increase=percent_increase,
            )
        )
    return sorted(anomalies, key=lambda anomaly: -anomaly.percent_increase)
