-- V1_0__initial_schema.sql
-- Fase 0: solo tablas de infraestructura (auth + sincronización + FX).
-- Las tablas de dominio (accounts, categories, transactions, ...) llegan
-- en V1_1 junto con Fase 1. Una migración aplicada NUNCA se edita.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,
    name            VARCHAR(255) NOT NULL,
    base_currency   VARCHAR(3) NOT NULL DEFAULT 'GTQ',
    timezone        VARCHAR(64) NOT NULL DEFAULT 'America/Guatemala',
    locale          VARCHAR(10) NOT NULL DEFAULT 'es-GT',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- id lo genera el cliente (necesita una identidad estable entre reinicios
-- de la app; la reusa como device_id en login/refresh/sync).
CREATE TABLE devices (
    id              UUID PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id),
    platform        VARCHAR(20) NOT NULL,
    push_token      VARCHAR(255),
    app_version     VARCHAR(20),
    last_sync_seq   BIGINT NOT NULL DEFAULT 0,
    last_seen_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_devices_user_id ON devices(user_id);

CREATE TABLE refresh_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    device_id       UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    token_hash      VARCHAR(64) NOT NULL UNIQUE,
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ,
    last_used_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_refresh_tokens_user_id ON refresh_tokens(user_id);
CREATE INDEX idx_refresh_tokens_device_id ON refresh_tokens(device_id);

-- Idempotencia: PK compuesta, TTL 24h purgado por job (Fase 3).
CREATE TABLE idempotency_keys (
    user_id         UUID NOT NULL REFERENCES users(id),
    key             VARCHAR(255) NOT NULL,
    endpoint        VARCHAR(255) NOT NULL,
    request_hash    VARCHAR(64) NOT NULL,
    response_body   JSONB NOT NULL,
    status_code     INTEGER NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, key)
);

-- Log de cambios: server_seq es el cursor monotónico global de /sync.
CREATE TABLE change_log (
    server_seq      BIGSERIAL PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id),
    entity_type     VARCHAR(64) NOT NULL,
    entity_id       UUID NOT NULL,
    op              VARCHAR(10) NOT NULL CHECK (op IN ('upsert', 'delete')),
    payload         JSONB NOT NULL,
    device_id       UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_change_log_user_seq ON change_log(user_id, server_seq);

CREATE TABLE fx_rates (
    date            DATE NOT NULL,
    from_currency   VARCHAR(3) NOT NULL,
    to_currency     VARCHAR(3) NOT NULL,
    rate            NUMERIC(18, 8) NOT NULL,
    source          VARCHAR(10) NOT NULL CHECK (source IN ('manual', 'api')),
    PRIMARY KEY (date, from_currency, to_currency)
);
