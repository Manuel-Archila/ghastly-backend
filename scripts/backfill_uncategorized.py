"""Asigna "Sin categoría" a los gastos que ya existían sin categoría.

    uv run python -m scripts.backfill_uncategorized            # simulación: solo cuenta
    uv run python -m scripts.backfill_uncategorized --apply    # aplica los cambios

Es idempotente: correrlo de nuevo no encuentra nada que hacer. Ver
`services/uncategorized_backfill.py` para el detalle. Antes de desplegar la regla
"un gasto no puede existir sin categoría", correr este script; después, una vez
más, para recoger lo que la app vieja haya creado mientras tanto.
"""

from __future__ import annotations

import argparse
import asyncio

from services.uncategorized_backfill import backfill_uncategorized
from storage.db import get_session_factory


async def main(apply: bool) -> None:
    async with get_session_factory()() as db:
        report = await backfill_uncategorized(db, apply=apply)

    mode = "APLICADO" if apply else "SIMULACIÓN (no se escribió nada)"
    print(f"[{mode}]")
    print(f"  usuarios con gastos sin categoría: {report.users}")
    print(f"  transacciones:      {report.transactions}")
    print(f"  reglas recurrentes: {report.recurring_rules}")
    print(f"  planes de cuotas:   {report.installment_plans}")
    print(f"  plantillas:         {report.templates}")
    print(f"  categorías 'Sin categoría' a crear: {report.categories_created}")
    if not apply and report.total_rows:
        print("Para aplicar: agregar --apply")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="aplicar los cambios")
    asyncio.run(main(parser.parse_args().apply))
