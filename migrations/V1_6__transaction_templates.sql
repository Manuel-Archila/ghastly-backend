-- V1_6__transaction_templates.sql
-- Fase 5: plantillas de gasto frecuente (PLAN-backend.md §5/§8).
-- El cliente las usa para prellenar POST /transactions; no hay un
-- transactions.template_id (a diferencia de refund_of_id/receivable_id) porque
-- una transacción creada desde una plantilla es una entidad plenamente
-- independiente, no conserva una relación contable con su origen.

CREATE TABLE transaction_templates (
    id              UUID PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id),
    name            VARCHAR(255) NOT NULL,
    account_id      UUID NOT NULL REFERENCES accounts(id),
    category_id     UUID REFERENCES categories(id),
    kind            VARCHAR(10) NOT NULL CHECK (kind IN ('expense', 'income')),
    amount_cents    BIGINT NOT NULL CHECK (amount_cents > 0),
    description     VARCHAR(500),
    use_count       INTEGER NOT NULL DEFAULT 0,
    last_used_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ,
    server_seq      BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX idx_transaction_templates_user_id ON transaction_templates(user_id) WHERE deleted_at IS NULL;
