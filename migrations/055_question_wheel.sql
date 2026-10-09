-- Roue « question offerte » (09/10/2026) : chaque tirage est journalisé ; un
-- tirage n'est possible qu'après une notification gift_question récente et
-- encore inutilisée.
CREATE TABLE IF NOT EXISTS question_wheel_spins (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS question_wheel_spins_user_created
    ON question_wheel_spins (user_id, created_at);
