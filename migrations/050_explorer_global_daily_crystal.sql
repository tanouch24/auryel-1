-- Temporary global Free Explorer quota and persisted Crystal Ball readings.
-- Additive: Rewarded Explorer ledgers remain intact for later reactivation.
CREATE TABLE IF NOT EXISTS explorer_daily_generation_usage (
    user_id          UUID NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    usage_day        DATE NOT NULL,
    generation_count INTEGER NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, usage_day),
    CONSTRAINT explorer_daily_generation_count_nonnegative
        CHECK (generation_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_explorer_daily_generation_user_day
    ON explorer_daily_generation_usage (user_id, usage_day DESC);

CREATE TABLE IF NOT EXISTS crystal_ball_readings (
    id             UUID PRIMARY KEY,
    user_id        UUID NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    advisor_id     TEXT NOT NULL,
    question       TEXT NOT NULL,
    theme          TEXT NOT NULL,
    vision_title   TEXT NOT NULL,
    vision         TEXT NOT NULL,
    interpretation TEXT NOT NULL,
    guidance       TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT crystal_ball_question_nonempty CHECK (length(btrim(question)) > 0),
    CONSTRAINT crystal_ball_idempotency_nonempty CHECK (length(btrim(idempotency_key)) > 0),
    CONSTRAINT crystal_ball_user_key_unique UNIQUE (user_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_crystal_ball_readings_user_created
    ON crystal_ball_readings (user_id, created_at DESC);
