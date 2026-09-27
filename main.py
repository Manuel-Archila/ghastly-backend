from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from config import get_settings
from core.middleware import RequestIdMiddleware, register_exception_handlers
from core.rate_limit import limiter
from core.response import fail
from jobs.scheduler import shutdown_scheduler, start_scheduler
from logger import configure_logging
from routers import (
    accounts,
    auth,
    budgets,
    categories,
    debts,
    devices,
    goals,
    health,
    installments,
    notification_preferences,
    receivables,
    recurring,
    reports,
    sync,
    transaction_templates,
    transactions,
)
from storage.db import get_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    start_scheduler()
    yield
    shutdown_scheduler()
    await get_engine().dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Ghastly API", version="0.1.0", lifespan=lifespan)

    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def handle_rate_limit(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        envelope = fail(
            "Demasiados intentos. Intenta de nuevo en un momento.", {"code": "RATE_LIMITED"}
        )
        return JSONResponse(status_code=429, content=envelope.model_dump())

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(devices.router)
    app.include_router(accounts.router)
    app.include_router(categories.router)
    app.include_router(transactions.router)
    app.include_router(sync.router)
    app.include_router(budgets.router)
    app.include_router(reports.router)
    app.include_router(installments.plans_router)
    app.include_router(installments.installments_router)
    app.include_router(recurring.router)
    app.include_router(recurring.subscriptions_router)
    app.include_router(debts.router)
    app.include_router(goals.router)
    app.include_router(receivables.router)
    app.include_router(notification_preferences.router)
    app.include_router(transaction_templates.router)

    return app


app = create_app()
