-- V1_7__account_minimum_payment.sql
-- Fase 5: GET /accounts/{id}/statement necesita un % de pago mínimo para
-- calcular minimum_cents. Sin valor por defecto: inventar un porcentaje
-- "típico" sería falsa precisión, varía por banco — el usuario lo
-- configura si lo conoce; si no, minimum_cents sale null.

ALTER TABLE accounts ADD COLUMN minimum_payment_percent NUMERIC(5,2);
