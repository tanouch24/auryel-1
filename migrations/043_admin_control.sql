-- AURYEL CONTROL admin security schema. Additive and idempotent.
-- No legacy admin table is altered and no mobile session is accepted here.

CREATE TABLE IF NOT EXISTS admin_accounts (
    id                  UUID PRIMARY KEY,
    email               TEXT NOT NULL,
    email_normalized    TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    role                TEXT NOT NULL DEFAULT 'admin',
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    mfa_enabled         BOOLEAN NOT NULL DEFAULT FALSE,
    mfa_method          TEXT,
    mfa_enrolled_at     TIMESTAMPTZ,
    mfa_secret_encrypted TEXT,
    mfa_pending_expires_at TIMESTAMPTZ,
    mfa_recovery_hashes JSONB NOT NULL DEFAULT '[]'::jsonb,
    mfa_last_step       BIGINT,
    mfa_recovery_generated_at TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at       TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    id                  UUID PRIMARY KEY,
    admin_id            UUID NOT NULL REFERENCES admin_accounts(id),
    token_hash          TEXT NOT NULL UNIQUE,
    expires_at          TIMESTAMPTZ NOT NULL,
    revoked_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at        TIMESTAMPTZ,
    ip_hash             TEXT,
    user_agent_hash     TEXT
);

CREATE INDEX IF NOT EXISTS idx_admin_sessions_admin_active
    ON admin_sessions (admin_id, revoked_at, expires_at);

CREATE TABLE IF NOT EXISTS admin_audit_events (
    id                  BIGSERIAL PRIMARY KEY,
    admin_id            UUID REFERENCES admin_accounts(id),
    action              TEXT NOT NULL,
    target_type         TEXT,
    target_id           TEXT,
    result              TEXT NOT NULL,
    metadata            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_events_created
    ON admin_audit_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_audit_events_admin_created
    ON admin_audit_events (admin_id, created_at DESC);

-- Read-only dashboard windows use these timestamp predicates. They are
-- additive and avoid full-table scans as the ledgers grow.
CREATE INDEX IF NOT EXISTS idx_accounts_admin_created
    ON accounts (created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_consultations_admin_created
    ON consultations (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_time_ledger_admin_created
    ON time_ledger (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reward_transactions_admin_created
    ON reward_transactions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mobile_subscriptions_admin_purchased
    ON mobile_subscriptions (purchased_at DESC);
CREATE INDEX IF NOT EXISTS idx_mobile_purchases_admin_purchased
    ON mobile_purchases (purchased_at DESC);
CREATE INDEX IF NOT EXISTS idx_notification_sends_admin_created
    ON notification_sends (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_app_sessions_admin_activity
    ON app_sessions (last_used_at DESC) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_push_devices_admin_platform
    ON push_devices (platform, enabled, revoked_at, invalid_at);
