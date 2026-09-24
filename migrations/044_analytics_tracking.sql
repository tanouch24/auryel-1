-- First-party analytics and cost observability. Additive, append-only,
-- idempotent. No private message or AI content is stored.

CREATE TABLE IF NOT EXISTS analytics_events (
    id               UUID PRIMARY KEY,
    event_name       TEXT NOT NULL,
    account_id       UUID,
    platform         TEXT,
    session_id_hash  TEXT,
    source           TEXT NOT NULL DEFAULT 'server',
    properties       JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at      TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    idempotency_key  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_analytics_events_account_idempotency
    ON analytics_events (account_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_analytics_events_name_time
    ON analytics_events (event_name, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_analytics_events_account_time
    ON analytics_events (account_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_analytics_events_platform_time
    ON analytics_events (platform, occurred_at DESC);

CREATE TABLE IF NOT EXISTS billing_events (
    id               UUID PRIMARY KEY,
    account_id       UUID,
    event_type       TEXT NOT NULL,
    store            TEXT,
    product_id       TEXT,
    currency         TEXT,
    gross_amount     NUMERIC(12, 4),
    transaction_hash TEXT,
    source           TEXT NOT NULL DEFAULT 'server',
    occurred_at      TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    idempotency_key  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_events_idempotency
    ON billing_events (account_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_billing_events_type_time
    ON billing_events (event_type, occurred_at DESC);

CREATE TABLE IF NOT EXISTS ai_usage (
    id              UUID PRIMARY KEY,
    request_id      TEXT NOT NULL UNIQUE,
    account_id      UUID,
    consultation_id UUID,
    advisor_id      TEXT,
    provider        TEXT,
    model           TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    total_tokens    INTEGER,
    latency_ms      INTEGER,
    status          TEXT NOT NULL,
    fallback_used   BOOLEAN NOT NULL DEFAULT FALSE,
    estimated_cost  NUMERIC(12, 8),
    currency        TEXT,
    cost_status     TEXT NOT NULL DEFAULT 'unconfigured',
    occurred_at     TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_account_time
    ON ai_usage (account_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_usage_time
    ON ai_usage (occurred_at DESC);

CREATE TABLE IF NOT EXISTS ai_pricing_config (
    id                    BIGSERIAL PRIMARY KEY,
    provider              TEXT NOT NULL,
    model                 TEXT NOT NULL,
    input_cost_per_token  NUMERIC(16, 12),
    output_cost_per_token NUMERIC(16, 12),
    currency              TEXT,
    effective_from        TIMESTAMPTZ NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ai_pricing_lookup
    ON ai_pricing_config (provider, model, effective_from DESC);

CREATE TABLE IF NOT EXISTS acquisition_attribution (
    id          UUID PRIMARY KEY,
    account_id  UUID NOT NULL,
    platform    TEXT,
    source      TEXT,
    campaign    TEXT,
    medium      TEXT,
    referral    TEXT,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_acquisition_account
    ON acquisition_attribution (account_id);
CREATE INDEX IF NOT EXISTS idx_acquisition_source_time
    ON acquisition_attribution (source, captured_at DESC);
