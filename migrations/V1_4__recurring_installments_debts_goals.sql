-- V1_4__recurring_installments_debts_goals.sql
-- Fase 3: los tres motores (recurrencia, cuotas, deudas) + metas.

CREATE TABLE recurring_rules (
    id                      UUID PRIMARY KEY,
    user_id                 UUID NOT NULL REFERENCES users(id),
    account_id              UUID NOT NULL REFERENCES accounts(id),
    category_id             UUID REFERENCES categories(id),
    kind                    VARCHAR(10) NOT NULL CHECK (kind IN ('expense', 'income')),
    name                    VARCHAR(255) NOT NULL,
    amount_cents            BIGINT NOT NULL CHECK (amount_cents > 0),
    currency                VARCHAR(3) NOT NULL DEFAULT 'GTQ',
    frequency               VARCHAR(10) NOT NULL
                            CHECK (frequency IN ('daily', 'weekly', 'monthly', 'quarterly', 'yearly')),
    interval                INTEGER NOT NULL DEFAULT 1 CHECK (interval > 0),
    next_due_date           DATE NOT NULL,
    end_date                DATE,
    auto_create             BOOLEAN NOT NULL DEFAULT true,
    reminder_days_before    INTEGER NOT NULL DEFAULT 1,
    status                  VARCHAR(10) NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'paused', 'ended')),
    last_generated_at       TIMESTAMPTZ,
    last_amount_cents       BIGINT,
    price_history           JSONB NOT NULL DEFAULT '[]',
    is_extraordinary        BOOLEAN NOT NULL DEFAULT false,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at              TIMESTAMPTZ,
    server_seq              BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX idx_recurring_rules_user_id ON recurring_rules(user_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_recurring_rules_next_due ON recurring_rules(next_due_date)
    WHERE deleted_at IS NULL AND status = 'active';

CREATE TABLE installment_plans (
    id                  UUID PRIMARY KEY,
    user_id             UUID NOT NULL REFERENCES users(id),
    account_id          UUID NOT NULL REFERENCES accounts(id),
    category_id         UUID REFERENCES categories(id),
    description         VARCHAR(500) NOT NULL,
    merchant             VARCHAR(255),
    total_amount_cents  BIGINT NOT NULL CHECK (total_amount_cents > 0),
    installments_count  INTEGER NOT NULL CHECK (installments_count > 0),
    first_payment_date  DATE NOT NULL,
    monthly_interest_rate NUMERIC(6, 4) NOT NULL DEFAULT 0,
    status              VARCHAR(10) NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'completed', 'cancelled')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ,
    server_seq          BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX idx_installment_plans_user_id ON installment_plans(user_id) WHERE deleted_at IS NULL;

CREATE TABLE installments (
    id              UUID PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id),
    plan_id         UUID NOT NULL REFERENCES installment_plans(id),
    number          INTEGER NOT NULL,
    due_date        DATE NOT NULL,
    amount_cents    BIGINT NOT NULL,
    principal_cents BIGINT NOT NULL,
    interest_cents  BIGINT NOT NULL DEFAULT 0,
    paid_at         TIMESTAMPTZ,
    transaction_id  UUID REFERENCES transactions(id),
    status          VARCHAR(10) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'paid', 'skipped')),
    server_seq      BIGINT NOT NULL DEFAULT 0,
    UNIQUE (plan_id, number)
);

CREATE INDEX idx_installments_plan_id ON installments(plan_id);
CREATE INDEX idx_installments_user_due ON installments(user_id, due_date) WHERE status = 'pending';

CREATE TABLE debts (
    id                      UUID PRIMARY KEY,
    user_id                 UUID NOT NULL REFERENCES users(id),
    name                    VARCHAR(255) NOT NULL,
    type                    VARCHAR(20) NOT NULL DEFAULT 'other'
                            CHECK (type IN ('personal_loan', 'mortgage', 'auto_loan', 'student_loan', 'other')),
    principal_cents         BIGINT NOT NULL CHECK (principal_cents > 0),
    balance_cents           BIGINT NOT NULL,
    monthly_interest_rate   NUMERIC(6, 4) NOT NULL DEFAULT 0,
    monthly_payment_cents   BIGINT,
    start_date              DATE NOT NULL,
    term_months             INTEGER,
    linked_account_id       UUID REFERENCES accounts(id),
    status                  VARCHAR(10) NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'paid_off')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at              TIMESTAMPTZ,
    server_seq              BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX idx_debts_user_id ON debts(user_id) WHERE deleted_at IS NULL;

CREATE TABLE debt_payments (
    id              UUID PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id),
    debt_id         UUID NOT NULL REFERENCES debts(id),
    date            DATE NOT NULL,
    total_cents     BIGINT NOT NULL,
    principal_cents BIGINT NOT NULL,
    interest_cents  BIGINT NOT NULL DEFAULT 0,
    fees_cents      BIGINT NOT NULL DEFAULT 0,
    transaction_id  UUID REFERENCES transactions(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    server_seq      BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX idx_debt_payments_debt_id ON debt_payments(debt_id);

CREATE TABLE goals (
    id                  UUID PRIMARY KEY,
    user_id             UUID NOT NULL REFERENCES users(id),
    name                VARCHAR(255) NOT NULL,
    target_amount_cents BIGINT NOT NULL CHECK (target_amount_cents > 0),
    current_amount_cents BIGINT NOT NULL DEFAULT 0,
    target_date         DATE,
    linked_account_id   UUID REFERENCES accounts(id),
    icon                VARCHAR(50),
    status              VARCHAR(10) NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'completed')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ,
    server_seq          BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX idx_goals_user_id ON goals(user_id) WHERE deleted_at IS NULL;

CREATE TABLE goal_contributions (
    id              UUID PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id),
    goal_id         UUID NOT NULL REFERENCES goals(id),
    date            DATE NOT NULL,
    amount_cents    BIGINT NOT NULL,
    transaction_id  UUID REFERENCES transactions(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    server_seq      BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX idx_goal_contributions_goal_id ON goal_contributions(goal_id);

-- Ahora que existen, transactions puede enlazar a su origen (diferido desde
-- V1_1: "installment_id, recurring_rule_id ... se agregan cuando esas
-- tablas existan").
ALTER TABLE transactions ADD COLUMN installment_id UUID REFERENCES installments(id);
ALTER TABLE transactions ADD COLUMN recurring_rule_id UUID REFERENCES recurring_rules(id);
