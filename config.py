"""Configuración de la app vía variables de entorno (pydantic-settings)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"

    # Puerto 5433 en local: el 5432 del host ya lo usa otro proyecto.
    database_url: str = "postgresql+asyncpg://ghastly:ghastly@localhost:5433/ghastly"

    jwt_secret: str = "change-me-in-every-environment-min-32-bytes"
    jwt_algorithm: str = "HS256"
    jwt_access_expires_minutes: int = 15
    jwt_refresh_expires_days: int = 60

    allow_registration: bool = True

    cors_origins: str = "http://localhost:8081"

    # Zona de negocio: la define el servidor, no el dispositivo (CLAUDE.md).
    business_timezone: str = "America/Guatemala"

    # Fase 5 — recibos en S3. Vacío hasta entonces.
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_region: str | None = None
    s3_bucket_receipts: str | None = None

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
