-- AURYEL CONTROL MFA bootstrap sessions. Additive and idempotent.
-- Password-only bootstrap tokens may access only MFA enrollment endpoints.
ALTER TABLE admin_sessions
    ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'full';

-- Do not leave a pre-existing password-only session fully privileged after
-- this migration. Enrolled administrators retain their existing sessions;
-- non-enrolled administrators receive only the short bootstrap window.
UPDATE admin_sessions AS s
SET scope = 'mfa_bootstrap',
    expires_at = LEAST(s.expires_at, NOW() + INTERVAL '10 minutes')
FROM admin_accounts AS a
WHERE s.admin_id = a.id
  AND a.mfa_enabled = FALSE
  AND s.revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_admin_sessions_scope
    ON admin_sessions (scope, revoked_at, expires_at);
