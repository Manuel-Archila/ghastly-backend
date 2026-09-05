"""Filtros de query compartidos por todo lo que reporta sobre transacciones.

CLAUDE.md regla de negocio 1: las transferencias no son gasto ni ingreso.
`exclude_transfers()` es el único lugar que aplica ese filtro — todo query
de reporte (stats hoy; dashboard/cashflow/by-category en Fase 4) pasa por
aquí. Si escribís un reporte nuevo y no lo usás, el reporte está mal.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select

from storage.models.transaction import Transaction


def exclude_transfers(stmt: Select[Any]) -> Select[Any]:
    return stmt.where(Transaction.kind != "transfer")
