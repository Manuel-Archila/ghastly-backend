-- V1_10__recurring_rules_fx_rate.sql
-- Caso de negocio 4: una suscripción en moneda extranjera (ej. un
-- servicio cobrado en USD) necesita congelar su tasa de cambio, igual
-- que cualquier transacción. No hay de dónde refrescarla al momento de
-- auto-generar (sin fuente de FX, decisión ya tomada) — se congela una
-- sola vez, al crear la regla.

ALTER TABLE recurring_rules ADD COLUMN fx_rate NUMERIC(18,8);
