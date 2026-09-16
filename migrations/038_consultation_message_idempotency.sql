-- Idempotence serveur des intentions d'envoi Consultation.
-- Additif : aucun message, quota ou historique existant n'est modifié.
CREATE TABLE IF NOT EXISTS consultation_message_requests (
    user_id UUID NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    message_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'processing'
        CHECK (status IN ('processing', 'completed', 'failed')),
    response_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_consultation_message_requests_status
    ON consultation_message_requests (status, updated_at);
