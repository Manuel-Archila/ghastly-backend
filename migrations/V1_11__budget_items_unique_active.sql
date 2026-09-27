-- V1_11__budget_items_unique_active.sql
-- Un ítem de presupuesto se borra de forma lógica (deleted_at), pero el
-- UNIQUE (budget_id, category_id) original también contaba las filas borradas:
-- quitar una categoría del presupuesto y volver a agregarla después fallaba.
-- La unicidad pasa a valer solo entre ítems activos.

ALTER TABLE budget_items DROP CONSTRAINT budget_items_budget_id_category_id_key;

CREATE UNIQUE INDEX uq_budget_items_budget_category
    ON budget_items(budget_id, category_id)
    WHERE deleted_at IS NULL;
