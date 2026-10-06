-- Persisted structured Explorer readings for Dreams and Compatibility.
-- Additive: existing Tarot/Crystal and rewarded ledgers remain unchanged.
CREATE TABLE IF NOT EXISTS explorer_structured_readings (
    id              UUID PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    experience_type TEXT NOT NULL,
    advisor_id      TEXT NOT NULL,
    input_data      JSONB NOT NULL,
    result_data     JSONB NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT explorer_structured_type_valid
        CHECK (experience_type IN ('dreams', 'compatibility')),
    CONSTRAINT explorer_structured_key_nonempty
        CHECK (length(btrim(idempotency_key)) > 0),
    CONSTRAINT explorer_structured_input_object
        CHECK (jsonb_typeof(input_data) = 'object'),
    CONSTRAINT explorer_structured_result_object
        CHECK (jsonb_typeof(result_data) = 'object'),
    CONSTRAINT explorer_structured_user_key_unique
        UNIQUE (user_id, experience_type, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_explorer_structured_user_created
    ON explorer_structured_readings (user_id, experience_type, created_at DESC);
