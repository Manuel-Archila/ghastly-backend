-- V1_8__budget_alerts_sent.sql
-- Fase 5: entrega push real de alertas de presupuesto (CLAUDE.md
-- "pendientes conocidos"). `check_alerts_for_category` se dispara en cada
-- escritura mientras la categoría siga sobre el umbral; sin esta tabla,
-- push real mandaría una notificación por cada gasto nuevo. La unicidad
-- (user_id, category_id, month, threshold) es lo que hace el INSERT ...
-- ON CONFLICT DO NOTHING atómico entre el gancho de escritura y el job de
-- las 20:00 corriendo casi al mismo tiempo.
--
-- Tabla de infraestructura, no de dominio: no sincroniza al cliente, sin
-- deleted_at/server_seq, PK propia (mismo criterio que idempotency_keys).

CREATE TABLE budget_alerts_sent (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id),
    category_id UUID NOT NULL,
    month VARCHAR(7) NOT NULL,
    threshold SMALLINT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, category_id, month, threshold)
);
