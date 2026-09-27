-- V1_9__notification_preferences.sql
-- PLAN-backend.md §5: singleton por usuario para los defaults que la
-- entrega push real (V1_8) dejó hardcodeados — umbrales de presupuesto,
-- días de aviso de cuotas, horas de silencio y canales habilitados.
--
-- Tabla de configuración, no de dominio: se edita online vía REST directo
-- (GET/PATCH /v1/notification-preferences), mismo criterio que el perfil
-- en `users` — no sincroniza por /sync/push, sin deleted_at/server_seq.

CREATE TABLE notification_preferences (
    user_id UUID PRIMARY KEY REFERENCES users(id),
    budget_alert_thresholds SMALLINT[] NOT NULL DEFAULT '{80,100}',
    due_reminder_days SMALLINT NOT NULL DEFAULT 3,
    quiet_hours_start TIME,
    quiet_hours_end TIME,
    channels TEXT[] NOT NULL DEFAULT '{push}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
