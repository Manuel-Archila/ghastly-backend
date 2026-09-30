"""`scripts/backfill_uncategorized`: los gastos que ya existían sin categoría pasan
a "Sin categoría", y los teléfonos se enteran por el change_log."""

import uuid

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from services.uncategorized_backfill import backfill_uncategorized
from tests.api.helpers import (
    create_account,
    create_category,
    default_expense_category,
    register_and_login,
)


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def _seed_expense_data(
    client: AsyncClient, headers: dict[str, str]
) -> tuple[uuid.UUID, uuid.UUID]:
    """Crea por la API un gasto, un ingreso, una regla, un plan y una plantilla.
    Devuelve (id del gasto, id del ingreso)."""
    account_id = await create_account(client, headers, initial_balance_cents=1_000_000)
    category_id = await default_expense_category(client, headers)
    income_category = await create_category(client, headers, kind="income", name="Salario")

    expense_id, income_id = uuid.uuid4(), uuid.uuid4()
    for payload in (
        {"id": str(expense_id), "kind": "expense", "category_id": str(category_id)},
        {"id": str(income_id), "kind": "income", "category_id": str(income_category)},
    ):
        response = await client.post(
            "/v1/transactions",
            json={
                "account_id": str(account_id),
                "amount_cents": 5_000,
                "date": "2026-09-05",
                **payload,
            },
            headers={**headers, **_idem()},
        )
        assert response.status_code == 200, response.text

    rule = await client.post(
        "/v1/recurring-rules",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(category_id),
            "name": "Netflix",
            "amount_cents": 8_900,
            "frequency": "monthly",
            "next_due_date": "2026-09-10",
        },
        headers=headers,
    )
    assert rule.status_code == 200, rule.text
    plan = await client.post(
        "/v1/installment-plans",
        json={
            "id": str(uuid.uuid4()),
            "account_id": str(account_id),
            "category_id": str(category_id),
            "description": "Refri",
            "total_amount_cents": 120_000,
            "installments_count": 1,
            "first_payment_date": "2026-09-15",
            "installment_ids": [str(uuid.uuid4())],
        },
        headers=headers,
    )
    assert plan.status_code == 200, plan.text
    template = await client.post(
        "/v1/transaction-templates",
        json={
            "id": str(uuid.uuid4()),
            "name": "Café",
            "account_id": str(account_id),
            "kind": "expense",
            "category_id": str(category_id),
            "amount_cents": 3_000,
        },
        headers=headers,
    )
    assert template.status_code == 200, template.text
    return expense_id, income_id


async def _make_legacy(engine: AsyncEngine) -> None:
    """Simula datos anteriores a la regla: gastos con la categoría en NULL,
    saltándose los servicios (que ya no lo permiten)."""
    async with engine.begin() as conn:
        for statement in (
            "UPDATE transactions SET category_id = NULL WHERE kind = 'expense'",
            "UPDATE recurring_rules SET category_id = NULL WHERE kind = 'expense'",
            "UPDATE installment_plans SET category_id = NULL",
            "UPDATE transaction_templates SET category_id = NULL WHERE kind = 'expense'",
        ):
            await conn.execute(text(statement))


async def _count_null_expense_rows(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        total = 0
        for query in (
            "SELECT count(*) FROM transactions WHERE kind = 'expense' AND category_id IS NULL",
            "SELECT count(*) FROM recurring_rules WHERE kind = 'expense' AND category_id IS NULL",
            "SELECT count(*) FROM installment_plans WHERE category_id IS NULL",
            "SELECT count(*) FROM transaction_templates "
            "WHERE kind = 'expense' AND category_id IS NULL",
        ):
            total += (await conn.execute(text(query))).scalar_one()
        return total


async def test_dry_run_counts_but_writes_nothing(
    client: AsyncClient, async_engine: AsyncEngine
) -> None:
    headers = await register_and_login(client)
    await _seed_expense_data(client, headers)
    await _make_legacy(async_engine)

    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as db:
        report = await backfill_uncategorized(db, apply=False)

    assert (report.users, report.transactions, report.recurring_rules) == (1, 1, 1)
    assert (report.installment_plans, report.templates, report.categories_created) == (1, 1, 1)
    assert await _count_null_expense_rows(async_engine) == 4  # nada cambió


async def test_apply_moves_every_uncategorized_expense_to_sin_categoria(
    client: AsyncClient, async_engine: AsyncEngine
) -> None:
    headers = await register_and_login(client)
    _expense_id, income_id = await _seed_expense_data(client, headers)
    await _make_legacy(async_engine)

    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as db:
        report = await backfill_uncategorized(db, apply=True)
    assert report.total_rows == 4

    assert await _count_null_expense_rows(async_engine) == 0
    async with async_engine.connect() as conn:
        names = (
            await conn.execute(
                text("SELECT name, kind FROM categories WHERE name = 'Sin categoría'")
            )
        ).all()
        assert [tuple(row) for row in names] == [("Sin categoría", "expense")]  # una sola, de gasto
        # Un ingreso sin categoría sigue igual: la regla es solo para gastos.
        income_category = (
            await conn.execute(
                text("SELECT category_id FROM transactions WHERE id = :id"), {"id": income_id}
            )
        ).scalar_one()
        assert income_category is not None  # este ingreso sí traía la suya, no se toca


async def test_apply_is_idempotent(client: AsyncClient, async_engine: AsyncEngine) -> None:
    headers = await register_and_login(client)
    await _seed_expense_data(client, headers)
    await _make_legacy(async_engine)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async with factory() as db:
        await backfill_uncategorized(db, apply=True)
    async with factory() as db:
        second = await backfill_uncategorized(db, apply=True)

    assert second.total_rows == 0
    assert second.users == 0
    assert second.categories_created == 0
    async with async_engine.connect() as conn:
        count = (
            await conn.execute(text("SELECT count(*) FROM categories WHERE name = 'Sin categoría'"))
        ).scalar_one()
        assert count == 1


async def test_reuses_an_existing_sin_categoria(
    client: AsyncClient, async_engine: AsyncEngine
) -> None:
    headers = await register_and_login(client)
    existing = await create_category(client, headers, name="Sin categoría")
    await _seed_expense_data(client, headers)
    await _make_legacy(async_engine)

    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as db:
        report = await backfill_uncategorized(db, apply=True)

    assert report.categories_created == 0
    async with async_engine.connect() as conn:
        used = (
            (
                await conn.execute(
                    text("SELECT DISTINCT category_id FROM transactions WHERE kind = 'expense'")
                )
            )
            .scalars()
            .all()
        )
        assert used == [existing]


async def test_phones_see_the_change_through_sync_pull(
    client: AsyncClient, async_engine: AsyncEngine
) -> None:
    headers = await register_and_login(client)
    expense_id, _income_id = await _seed_expense_data(client, headers)
    before = (await client.get("/v1/sync/pull?since=0", headers=headers)).json()["data"]["next_seq"]
    await _make_legacy(async_engine)

    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as db:
        await backfill_uncategorized(db, apply=True)

    pull = await client.get(f"/v1/sync/pull?since={before}", headers=headers)
    changes = pull.json()["data"]["changes"]
    sin_categoria = next(
        c
        for c in changes
        if c["entity_type"] == "category" and c["payload"]["name"] == "Sin categoría"
    )
    moved = next(
        c
        for c in changes
        if c["entity_type"] == "transaction" and c["entity_id"] == str(expense_id)
    )
    assert moved["payload"]["category_id"] == sin_categoria["entity_id"]


async def test_other_users_are_untouched(client: AsyncClient, async_engine: AsyncEngine) -> None:
    headers_a = await register_and_login(client, "a@example.com")
    headers_b = await register_and_login(client, "b@example.com")
    await _seed_expense_data(client, headers_a)
    b_expense, _ = await _seed_expense_data(client, headers_b)

    # Solo el usuario A queda con datos "viejos".
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE transactions SET category_id = NULL WHERE kind = 'expense' "
                "AND user_id = (SELECT id FROM users WHERE email = 'a@example.com')"
            )
        )

    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as db:
        report = await backfill_uncategorized(db, apply=True)

    assert report.users == 1
    async with async_engine.connect() as conn:
        b_category = (
            await conn.execute(
                text("SELECT category_id FROM transactions WHERE id = :id"), {"id": b_expense}
            )
        ).scalar_one()
        assert b_category is not None
        b_sin = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM categories c JOIN users u ON u.id = c.user_id "
                    "WHERE c.name = 'Sin categoría' AND u.email = 'b@example.com'"
                )
            )
        ).scalar_one()
        assert b_sin == 0
