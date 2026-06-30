-- migrations/006_meta_message_id.sql
-- Colonne WAMID (WhatsApp Message ID) sur la table messages.
-- Permet la déduplication des webhooks Meta retentés (retry automatique).
-- Déjà appliqué en production via init_db() migration v11.

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS meta_message_id TEXT DEFAULT '';

-- Index unique partiel : exclut les lignes sans WAMID (réponses bot, onboarding)
-- pour ne contraindre que les messages entrants dédupliqués.
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_meta_msg
    ON messages (meta_message_id)
    WHERE meta_message_id != '';
