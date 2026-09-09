-- V1_5__receivables.sql
-- Fase 5: gastos compartidos (caso de negocio 3, PLAN-backend.md §6).
-- Un gasto puede repartirse entre varias personas: cada una que debe su
-- parte es un receivable propio contra la misma transaction_id.

CREATE TABLE receivables (
    id                          UUID PRIMARY KEY,
    user_id                     UUID NOT NULL REFERENCES users(id),
    transaction_id              UUID NOT NULL REFERENCES transactions(id),
    counterparty                VARCHAR(255) NOT NULL,
    amount_cents                BIGINT NOT NULL CHECK (amount_cents > 0),
    settled_at                  TIMESTAMPTZ,
    settlement_transaction_id   UUID REFERENCES transactions(id),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at                  TIMESTAMPTZ,
    server_seq                  BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX idx_receivables_user_id ON receivables(user_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_receivables_transaction_id ON receivables(transaction_id);

-- Liga la transacción de INGRESO creada al liquidar (`POST
-- /receivables/{id}/settle`) con el receivable que salda — mismo patrón
-- que refund_of_id para reembolsos. `receivables.settlement_transaction_id`
-- es el puntero inverso, hacia esa misma transacción.
ALTER TABLE transactions ADD COLUMN receivable_id UUID REFERENCES receivables(id);
