-- AdMob Rewarded SSV : état de session et transactions Google.
-- Additif/idempotent : aucun wallet, ledger ou historique n'est supprimé.
CREATE TABLE IF NOT EXISTS admob_reward_sessions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES accounts(user_id),
    ad_unit TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    credited_transaction_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_admob_reward_sessions_user_created
    ON admob_reward_sessions (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS admob_reward_events (
    transaction_id TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES accounts(user_id),
    session_id UUID REFERENCES admob_reward_sessions(id),
    ad_unit TEXT NOT NULL,
    reward_amount INTEGER NOT NULL,
    reward_item TEXT NOT NULL,
    ad_network TEXT,
    callback_timestamp_ms BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    credited_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_admob_reward_events_user_created
    ON admob_reward_events (user_id, created_at DESC);
