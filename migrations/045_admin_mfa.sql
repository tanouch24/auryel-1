-- AURYEL CONTROL TOTP MFA. Additive and idempotent; no production data deletion.
ALTER TABLE admin_accounts
    ADD COLUMN IF NOT EXISTS mfa_secret_encrypted TEXT,
    ADD COLUMN IF NOT EXISTS mfa_pending_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS mfa_recovery_hashes JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS mfa_last_step BIGINT,
    ADD COLUMN IF NOT EXISTS mfa_recovery_generated_at TIMESTAMPTZ;
