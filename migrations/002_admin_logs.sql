-- migrations/002_admin_logs.sql
-- Table de traçabilité des actions admin (LOT 7.6)
-- Déjà appliqué en production via init_db() migration v7.

CREATE TABLE IF NOT EXISTS admin_logs (
    id        SERIAL PRIMARY KEY,
    action    TEXT,
    phone     TEXT,
    detail    TEXT,
    timestamp TEXT
);
