"""Job de las 07:00: actualiza tasas de cambio desde una fuente externa.

No hay fuente configurada todavía (PLAN-backend §14 — pregunta abierta:
"¿fuente de tasas de cambio, o solo carga manual?"). No-op honesto, no una
llamada simulada — cuando se decida la fuente, esto deja de ser no-op."""

from __future__ import annotations

import structlog

logger = structlog.get_logger("jobs.refresh_fx_rates")


async def run() -> None:
    logger.info("refresh_fx_rates_skipped", reason="no fx source configured yet")
