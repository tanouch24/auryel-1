-- Rewarded V1: additive replacement for the historical Stars economy.
-- Historical reward_wallet/reward_transactions rows remain untouched and are
-- no longer read by the Rewarded product flow.
CREATE TABLE IF NOT EXISTS rewarded_entitlements (
    user_id                     UUID PRIMARY KEY,
    questions_available         INTEGER NOT NULL DEFAULT 0 CHECK (questions_available >= 0),
    rewarded_progress           INTEGER NOT NULL DEFAULT 0 CHECK (rewarded_progress >= 0 AND rewarded_progress < 10),
    rewarded_total              BIGINT NOT NULL DEFAULT 0 CHECK (rewarded_total >= 0),
    rewarded_minutes_awarded    BIGINT NOT NULL DEFAULT 0 CHECK (rewarded_minutes_awarded >= 0),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rewarded_question_reservations (
    id              UUID PRIMARY KEY,
    user_id         UUID NOT NULL,
    idempotency_key TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'reserved'
                    CHECK (status IN ('reserved', 'consumed', 'released')),
    consultation_id UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    consumed_at     TIMESTAMPTZ,
    UNIQUE (user_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_rewarded_question_reservations_user
    ON rewarded_question_reservations (user_id, created_at DESC);

DO $$
BEGIN
    ALTER TABLE rewarded_entitlements
        ADD CONSTRAINT fk_rewarded_entitlements_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$
BEGIN
    ALTER TABLE rewarded_question_reservations
        ADD CONSTRAINT fk_rewarded_question_reservations_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
