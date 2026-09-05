-- V1_3__budgets.sql
-- Fase 2: presupuestos. budget_periods/budget_period_items congelan el
-- resultado al cerrar un mes — así una transacción retroactiva no reescribe
-- la historia de un mes ya cerrado (PLAN-backend §5, regla 11).

CREATE TABLE budgets (
    id                  UUID PRIMARY KEY,
    user_id             UUID NOT NULL REFERENCES users(id),
    name                VARCHAR(255) NOT NULL,
    period_type         VARCHAR(10) NOT NULL DEFAULT 'monthly'
                        CHECK (period_type IN ('monthly', 'weekly', 'custom')),
    is_active           BOOLEAN NOT NULL DEFAULT true,
    -- Default para budget_items sin override propio.
    rollover_enabled    BOOLEAN NOT NULL DEFAULT false,
    global_limit_cents  BIGINT,
    income_basis        VARCHAR(20) NOT NULL DEFAULT 'fixed'
                        CHECK (income_basis IN ('fixed', 'previous_month', 'avg_3m')),
    fixed_income_cents  BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ,
    server_seq          BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX idx_budgets_user_id ON budgets(user_id) WHERE deleted_at IS NULL;

CREATE TABLE budget_items (
    id                  UUID PRIMARY KEY,
    user_id             UUID NOT NULL REFERENCES users(id),
    budget_id           UUID NOT NULL REFERENCES budgets(id),
    category_id         UUID NOT NULL REFERENCES categories(id),
    amount_cents        BIGINT NOT NULL CHECK (amount_cents >= 0),
    -- NULL = usa el default de budgets.rollover_enabled.
    rollover_enabled    BOOLEAN,
    sort_order          INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ,
    server_seq          BIGINT NOT NULL DEFAULT 0,
    UNIQUE (budget_id, category_id)
);

CREATE INDEX idx_budget_items_budget_id ON budget_items(budget_id) WHERE deleted_at IS NULL;

-- Un período cerrado por mes ('YYYY-MM'). Se crea al llamar close-period;
-- mientras el mes está en curso, /budgets/current calcula en vivo y esta
-- tabla no tiene fila para ese mes.
CREATE TABLE budget_periods (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES users(id),
    budget_id               UUID NOT NULL REFERENCES budgets(id),
    month                   VARCHAR(7) NOT NULL,
    period_start            DATE NOT NULL,
    period_end              DATE NOT NULL,
    expected_income_cents   BIGINT,
    closed_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    server_seq              BIGINT NOT NULL DEFAULT 0,
    UNIQUE (budget_id, month)
);

CREATE INDEX idx_budget_periods_budget_id ON budget_periods(budget_id);

CREATE TABLE budget_period_items (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    budget_period_id    UUID NOT NULL REFERENCES budget_periods(id),
    category_id         UUID NOT NULL REFERENCES categories(id),
    budgeted_cents      BIGINT NOT NULL,
    rollover_in_cents   BIGINT NOT NULL DEFAULT 0,
    spent_cents         BIGINT NOT NULL,
    rollover_out_cents  BIGINT NOT NULL DEFAULT 0,
    UNIQUE (budget_period_id, category_id)
);

CREATE INDEX idx_budget_period_items_period_id ON budget_period_items(budget_period_id);

-- Nota: `transactions.affects_closed_period` ya existe desde V1_1 (regla 11).
-- El service lo marca comparando `date` contra `budget_periods.month` al crear
-- o editar una transacción con fecha retroactiva.
