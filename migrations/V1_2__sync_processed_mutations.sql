-- V1_2__sync_processed_mutations.sql
-- Dedup de /sync/push: reenviar el mismo lote (mismo client_mutation_id)
-- es no-op — se devuelve el resultado ya calculado, no se reaplica nada.

CREATE TABLE processed_mutations (
    user_id             UUID NOT NULL REFERENCES users(id),
    client_mutation_id  UUID NOT NULL,
    entity_type         VARCHAR(64) NOT NULL,
    entity_id           UUID NOT NULL,
    outcome             VARCHAR(20) NOT NULL CHECK (outcome IN ('applied', 'conflict')),
    reason              VARCHAR(255),
    server_payload      JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, client_mutation_id)
);
