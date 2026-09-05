-- V1_1__accounts_categories_transactions.sql
-- Fase 1: núcleo transaccional. installment_id, recurring_rule_id y
-- receivable_id en `transactions` se agregan con ALTER TABLE cuando esas
-- tablas existan (Fase 3/5) — nunca se edita esta migración.

CREATE TABLE accounts (
    id                      UUID PRIMARY KEY,
    user_id                 UUID NOT NULL REFERENCES users(id),
    name                    VARCHAR(255) NOT NULL,
    type                    VARCHAR(20) NOT NULL
                            CHECK (type IN ('checking', 'savings', 'credit_card', 'cash',
                                             'investment', 'loan', 'digital_wallet')),
    currency                VARCHAR(3) NOT NULL DEFAULT 'GTQ',
    institution             VARCHAR(255),
    last_four               VARCHAR(4),
    initial_balance_cents   BIGINT NOT NULL DEFAULT 0,
    current_balance_cents   BIGINT NOT NULL DEFAULT 0,
    is_archived             BOOLEAN NOT NULL DEFAULT false,
    color                   VARCHAR(20),
    icon                    VARCHAR(50),
    sort_order              INTEGER NOT NULL DEFAULT 0,
    -- Solo aplican a type='credit_card':
    credit_limit_cents      BIGINT,
    statement_day           SMALLINT CHECK (statement_day BETWEEN 1 AND 31),
    payment_due_day         SMALLINT CHECK (payment_due_day BETWEEN 1 AND 31),
    interest_rate           NUMERIC(6, 4),
    balance_recalculated_at TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at              TIMESTAMPTZ,
    server_seq              BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX idx_accounts_user_id ON accounts(user_id) WHERE deleted_at IS NULL;

CREATE TABLE categories (
    id                  UUID PRIMARY KEY,
    user_id             UUID NOT NULL REFERENCES users(id),
    name                VARCHAR(255) NOT NULL,
    kind                VARCHAR(10) NOT NULL CHECK (kind IN ('expense', 'income')),
    parent_id           UUID REFERENCES categories(id),
    icon                VARCHAR(50),
    color               VARCHAR(20),
    is_archived         BOOLEAN NOT NULL DEFAULT false,
    is_tax_deductible   BOOLEAN NOT NULL DEFAULT false,
    sort_order          INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ,
    server_seq          BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX idx_categories_user_id ON categories(user_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_categories_parent_id ON categories(parent_id);

CREATE TABLE transactions (
    id                  UUID PRIMARY KEY,
    user_id             UUID NOT NULL REFERENCES users(id),
    account_id          UUID NOT NULL REFERENCES accounts(id),
    category_id         UUID REFERENCES categories(id),
    kind                VARCHAR(10) NOT NULL CHECK (kind IN ('expense', 'income', 'transfer')),
    amount_cents        BIGINT NOT NULL CHECK (amount_cents > 0),
    currency            VARCHAR(3) NOT NULL DEFAULT 'GTQ',
    fx_rate             NUMERIC(18, 8),
    base_amount_cents   BIGINT,
    date                DATE NOT NULL,
    description         VARCHAR(500),
    merchant            VARCHAR(255),
    notes               TEXT,
    -- Transferencias: dos filas con el mismo transfer_group_id, direcciones opuestas.
    transfer_group_id   UUID,
    transfer_direction  VARCHAR(3) CHECK (transfer_direction IN ('in', 'out')),
    refund_of_id        UUID REFERENCES transactions(id),
    is_reconciled       BOOLEAN NOT NULL DEFAULT false,
    is_tax_relevant     BOOLEAN NOT NULL DEFAULT false,
    is_extraordinary    BOOLEAN NOT NULL DEFAULT false,
    affects_closed_period BOOLEAN NOT NULL DEFAULT false,
    receipt_key         VARCHAR(500),
    tags                TEXT[] NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ,
    server_seq          BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT chk_transfer_has_direction
        CHECK (kind != 'transfer' OR transfer_direction IS NOT NULL)
);

CREATE INDEX idx_transactions_user_date ON transactions(user_id, date DESC);
CREATE INDEX idx_transactions_user_account_date ON transactions(user_id, account_id, date DESC);
CREATE INDEX idx_transactions_user_category_date ON transactions(user_id, category_id, date);
CREATE INDEX idx_transactions_transfer_group ON transactions(transfer_group_id)
    WHERE transfer_group_id IS NOT NULL;
CREATE INDEX idx_transactions_tags ON transactions USING GIN(tags);
CREATE INDEX idx_transactions_search ON transactions
    USING GIN(to_tsvector('spanish', coalesce(description, '') || ' ' || coalesce(merchant, '')));
