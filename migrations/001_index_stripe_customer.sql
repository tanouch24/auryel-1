-- migrations/001_index_stripe_customer.sql
-- Index unique partiel sur stripe_customer_id (LOT 7.2 — commit 0efc808)
-- Exclut la valeur '' partagée par tous les non-abonnés.
-- Déjà appliqué en production via init_db() migration v6.

CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_stripe_customer
    ON users (stripe_customer_id)
    WHERE stripe_customer_id != '';
