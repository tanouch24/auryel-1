-- Événements non lus ciblés par compte.
-- Additif, idempotent, sans rétroactivité : aucun badge historique n'est créé.
CREATE TABLE IF NOT EXISTS unread_events (
    id            BIGSERIAL PRIMARY KEY,
    user_id       UUID NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    category      TEXT NOT NULL CHECK (category IN ('consultation', 'wellbeing')),
    event_type    TEXT NOT NULL,
    reference_key TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    read_at       TIMESTAMPTZ NULL,
    dedupe_key    TEXT NOT NULL,
    CONSTRAINT uq_unread_events_dedupe UNIQUE (user_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_unread_events_user_unread
    ON unread_events (user_id, category, created_at DESC)
    WHERE read_at IS NULL;
