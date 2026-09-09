-- 015_ai_reports.sql — Migration v41 : SIGNALEMENT IA (« Signaler cette réponse »).
--
-- Miroir LISIBLE du bloc « Migration v41 » exécuté réellement par init_db()
-- dans auryel_bot.py. PUREMENT ADDITIF : 1 table neuve + 1 FK idempotente.
-- Aucune colonne existante ALTER-ée, aucun backfill, aucun DROP / TRUNCATE /
-- DELETE, aucune donnée existante modifiée.
--
--   ai_reports
--     UN signalement = UNE ligne, créée par POST /api/app/ai/report.
--     `user_id`         : identité issue UNIQUEMENT du jeton Bearer côté route,
--                         jamais du body. FK -> accounts(user_id).
--     `message_id`      : messages.id (BIGINT) du message ASSISTANT visé. La
--                         route vérifie AVANT insertion que ce message existe,
--                         appartient au compte courant, et a bien role
--                         'assistant' (un message utilisateur -> 400). NULL si
--                         le signalement ne porte que sur une consultation.
--     `consultation_id` : fil concerné. Dérivé de messages.consultation_id
--                         quand message_id est fourni ; sinon vérifié
--                         appartenir au compte (consultations.user_id).
--     `reason`          : valeur d'une allowlist applicative (_AI_REPORT_REASONS
--                         = inappropriate / unsafe / misleading / other). Tout
--                         autre motif -> 400, aucune insertion.
--     `comment`         : texte utilisateur FACULTATIF, borné
--                         (_AI_REPORT_COMMENT_MAX = 1000) et échappé
--                         (_support_escape, anti-injection HTML). JAMAIS le
--                         contenu de la consultation : rien n'est recopié
--                         automatiquement depuis les messages.
--     `status`          : démarre à 'received'. Le texte renvoyé au client ne
--                         promet AUCUNE modération humaine.
--
--   Idempotence : deux index UNIQUE PARTIELS.
--     uq_ai_reports_message       (user_id, message_id, reason)  WHERE message_id IS NOT NULL
--     uq_ai_reports_consultation  (user_id, consultation_id, reason)
--                                 WHERE message_id IS NULL AND consultation_id IS NOT NULL
--   La route fait un SELECT préalable : un re-signalement identique ne crée pas
--   de doublon et renvoie la MÊME réponse 200 {"status": "received"}.
--
-- FK ai_reports.user_id -> accounts(user_id) SANS ON DELETE CASCADE : cohérent
-- avec le reste du schéma app — DELETE /api/app/account purge explicitement la
-- table (cf. _ACCOUNT_DELETE_CHILD_TABLES). Bloc DO $$ idempotent
-- (n'attrape que duplicate_object).
--
-- Idempotence init_db() : CREATE TABLE / INDEX IF NOT EXISTS = no-op au rejeu ;
-- la FK est protégée par le DO $$.

CREATE TABLE IF NOT EXISTS ai_reports (
    id               UUID         PRIMARY KEY,
    user_id          UUID         NOT NULL,
    consultation_id  UUID,
    message_id       BIGINT,
    reason           TEXT         NOT NULL,
    comment          TEXT,
    status           TEXT         NOT NULL DEFAULT 'received',
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_reports_message
ON ai_reports (user_id, message_id, reason)
WHERE message_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_reports_consultation
ON ai_reports (user_id, consultation_id, reason)
WHERE message_id IS NULL AND consultation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ai_reports_status
ON ai_reports (status, created_at DESC);

DO $$
BEGIN
    ALTER TABLE ai_reports
        ADD CONSTRAINT fk_ai_reports_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
