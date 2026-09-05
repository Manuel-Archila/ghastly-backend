"""Fixtures de tests/api: Postgres real vía testcontainers + httpx contra la app ASGI.

Requieren Docker corriendo (CLAUDE.md). `tests/domain` no depende de nada de esto.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from core.rate_limit import limiter
from dependencies import get_db


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16") as container:
        yield container


@pytest.fixture(scope="session")
def _migrated(postgres_container: PostgresContainer) -> None:
    pyway_bin = shutil.which("pyway")
    assert pyway_bin, "pyway no está instalado en el venv (uv sync --group dev)"
    host = postgres_container.get_container_host_ip()
    port = postgres_container.get_exposed_port(5432)
    subprocess.run(
        [
            pyway_bin,
            "migrate",
            "--database-type",
            "postgres",
            "--database-migration-dir",
            "migrations",
            "--database-table",
            "schema_version",
            "--database-host",
            host,
            "--database-port",
            str(port),
            "--database-name",
            postgres_container.dbname,
            "--database-username",
            postgres_container.username,
            "--database-password",
            postgres_container.password,
        ],
        check=True,
    )


@pytest_asyncio.fixture
async def async_engine(
    postgres_container: PostgresContainer, _migrated: None
) -> AsyncIterator[AsyncEngine]:
    # Scope de función, no de sesión: pytest-asyncio (modo auto) crea un
    # event loop nuevo por test, y un engine asyncpg queda atado al loop en
    # el que nace. Reusar uno entre tests revienta con
    # "attached to a different loop". El contenedor de Postgres sí se
    # reusa (es Docker, no asyncio).
    host = postgres_container.get_container_host_ip()
    port = postgres_container.get_exposed_port(5432)
    url = (
        f"postgresql+asyncpg://{postgres_container.username}:{postgres_container.password}"
        f"@{host}:{port}/{postgres_container.dbname}"
    )
    engine = create_async_engine(url, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(async_engine: AsyncEngine) -> AsyncIterator[None]:
    yield
    limiter.reset()
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE change_log, refresh_tokens, devices, "
                "idempotency_keys, fx_rates, users RESTART IDENTITY CASCADE"
            )
        )


@pytest_asyncio.fixture
async def client(async_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    from main import app

    session_factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def _override_get_db() -> AsyncIterator[object]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
