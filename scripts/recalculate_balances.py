"""Recalcula `current_balance_cents` de TODAS las cuentas desde su ledger
completo (`services/account_service.recalculate_account`, la misma función
que usa `POST /accounts/{id}/recalculate`).

    uv run python -m scripts.recalculate_balances            # simulación: solo muestra diffs
    uv run python -m scripts.recalculate_balances --apply    # aplica los cambios

Hace falta después de un bug donde el saldo cacheado se desincronizó del
ledger real (p. ej. `delete_transaction` no lo revertía) — recalcular desde
cero corrige cualquier cuenta afectada sin necesidad de saber cuáles son.
Es idempotente: una cuenta ya correcta no cambia. Cada cuenta que cambia
emite su entrada de `change_log` (igual que el endpoint), así que el
teléfono la recoge solo en el próximo `/sync/pull` — no hace falta tocar
nada del cliente.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from domain.balances import LedgerEntry, compute_balance
from services import account_service
from storage.db import get_session_factory
from storage.models.account import Account
from storage.models.transaction import Transaction


async def main(apply: bool) -> None:
    session_factory = get_session_factory()
    changed = 0
    total = 0

    async with session_factory() as db:
        result = await db.execute(select(Account).where(Account.deleted_at.is_(None)))
        accounts = list(result.scalars().all())

        for account in accounts:
            total += 1
            before = account.current_balance_cents
            if apply:
                await account_service.recalculate_account(db, account.user_id, account.id)
                after = account.current_balance_cents
            else:
                # Mismo cálculo que recalculate_account pero sin escribir nada.
                txns = await db.execute(
                    select(Transaction)
                    .where(Transaction.account_id == account.id, Transaction.deleted_at.is_(None))
                    .order_by(Transaction.date, Transaction.created_at)
                )
                entries = [
                    LedgerEntry(
                        kind=t.kind,  # type: ignore[arg-type]
                        amount_cents=t.amount_cents,
                        transfer_direction=t.transfer_direction,  # type: ignore[arg-type]
                    )
                    for t in txns.scalars().all()
                ]
                after = compute_balance(account.initial_balance_cents, entries, account.type)  # type: ignore[arg-type]

            if before != after:
                changed += 1
                print(f"  {account.name} ({account.id}): {before} -> {after}")

        if not apply:
            await db.rollback()

    mode = "APLICADO" if apply else "SIMULACIÓN (no se escribió nada)"
    print(f"[{mode}] cuentas revisadas: {total}, con diferencia: {changed}")
    if not apply and changed:
        print("Para aplicar: agregar --apply")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="aplicar los cambios")
    asyncio.run(main(parser.parse_args().apply))
