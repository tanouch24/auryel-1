-- migrations/005_morning_routine.sql
-- 6 colonnes pour la morning routine (envoi quotidien personnalisé).
-- Déjà appliqué en production via init_db() migration v10.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS last_morning_message_at  TEXT    DEFAULT '',
    ADD COLUMN IF NOT EXISTS last_template_message_at TEXT    DEFAULT '',
    ADD COLUMN IF NOT EXISTS templates_sans_reponse   INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS morning_messages_enabled BOOLEAN DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS free_entry_expires_at    TEXT    DEFAULT '',
    ADD COLUMN IF NOT EXISTS source_channel           TEXT    DEFAULT '';
