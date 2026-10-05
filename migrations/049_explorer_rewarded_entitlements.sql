-- Explorer rewarded entitlements, additive and account-owned.
-- Consultation rewards remain in rewarded_entitlements; this table is a
-- separate ledger for one generation of one Explorer experience.
ALTER TABLE admob_reward_sessions
    ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT 'consultation_question';
ALTER TABLE admob_reward_sessions
    ADD COLUMN IF NOT EXISTS experience_type TEXT;

CREATE TABLE IF NOT EXISTS explorer_generation_usage (
    user_id          UUID NOT NULL REFERENCES accounts(user_id),
    experience_type  TEXT NOT NULL,
    generation_count INTEGER NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, experience_type),
    CONSTRAINT explorer_generation_usage_count_nonnegative
        CHECK (generation_count >= 0)
);

CREATE TABLE IF NOT EXISTS explorer_reward_entitlements (
    id                UUID PRIMARY KEY,
    user_id           UUID NOT NULL REFERENCES accounts(user_id),
    experience_type   TEXT NOT NULL,
    reward_session_id UUID NOT NULL REFERENCES admob_reward_sessions(id),
    status            TEXT NOT NULL DEFAULT 'available',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    consumed_at       TIMESTAMPTZ,
    CONSTRAINT explorer_entitlement_status_valid
        CHECK (status IN ('available', 'consumed')),
    CONSTRAINT explorer_entitlement_session_unique
        UNIQUE (reward_session_id)
);
CREATE INDEX IF NOT EXISTS idx_explorer_entitlements_available
    ON explorer_reward_entitlements (user_id, experience_type, status, created_at);

CREATE TABLE IF NOT EXISTS explorer_generation_attempts (
    user_id          UUID NOT NULL REFERENCES accounts(user_id),
    experience_type  TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    result_id        UUID NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, experience_type, idempotency_key)
);
