-- 041_content_recommendations.sql — recommandations de contenu par conseiller.
-- Aucun verbatim de consultation n'est stocké : uniquement un code de thème,
-- l'identité du contenu validé et un snapshot d'affichage borné.
CREATE TABLE IF NOT EXISTS content_recommendations (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    advisor_id TEXT NOT NULL,
    content_type TEXT NOT NULL CHECK (content_type IN ('ebook', 'meditation', 'exercise')),
    content_id TEXT NOT NULL,
    title_snapshot TEXT NOT NULL,
    rationale_code TEXT NULL,
    content_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    card_displayed_at TIMESTAMPTZ NULL,
    opened_at TIMESTAMPTZ NULL,
    download_requested_at TIMESTAMPTZ NULL,
    follow_up_sent_at TIMESTAMPTZ NULL,
    follow_up_dedupe_key TEXT NULL,
    assistant_message_id BIGINT NULL REFERENCES messages(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_content_reco_user_created
    ON content_recommendations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_content_reco_user_type
    ON content_recommendations(user_id, content_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_content_reco_advisor
    ON content_recommendations(advisor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_content_reco_content
    ON content_recommendations(content_type, content_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_content_reco_follow_up
    ON content_recommendations(created_at, follow_up_sent_at)
    WHERE follow_up_sent_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_content_reco_follow_up_key
    ON content_recommendations(follow_up_dedupe_key)
    WHERE follow_up_dedupe_key IS NOT NULL;
