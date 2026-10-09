-- Réponse en suspens (09/10/2026) : quand le temps est épuisé, le guide
-- prépare une réponse dont SEULE la première phrase est montrée. On ne
-- conserve que cette phrase, pour que la réponse complète (après Premium)
-- commence exactement par elle.
CREATE TABLE IF NOT EXISTS consultation_teasers (
    id UUID PRIMARY KEY,
    user_id TEXT NOT NULL,
    advisor_id TEXT NOT NULL,
    first_sentence TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    consumed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS consultation_teasers_user_created
    ON consultation_teasers (user_id, created_at);
