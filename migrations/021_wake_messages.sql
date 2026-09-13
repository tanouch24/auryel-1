-- 021_wake_messages.sql — Migration v47 : RÉVEIL AURYEL, messages du matin.
--
-- Miroir LISIBLE du bloc « Migration v47 » exécuté réellement par init_db()
-- dans auryel_bot.py. PUREMENT ADDITIF : 1 table neuve + 1 index + un seed
-- idempotent de 60 phrases. Aucune colonne existante ALTER-ée, aucun
-- backfill, aucun DROP / TRUNCATE / DELETE. Aucune FK vers accounts : le
-- contenu est GLOBAL (même pour tous), donc absent de
-- _ACCOUNT_DELETE_CHILD_TABLES — comme meditation_catalog (v43) et
-- relaxation_video_catalog (v45).
--
--   wake_messages
--     Phrases du réveil vocal — texte source de vérité (PostgreSQL), audio
--     pré-généré hébergé ailleurs (Cloudflare R2) :
--       text         contenu affiché/lu, UNIQUE -> le seed (INSERT ...
--                    ON CONFLICT (text) DO NOTHING) est rejouable sans
--                    jamais dupliquer une phrase.
--       is_active    interrupteur (masque sans supprimer), nommé comme
--                    is_active partout ailleurs dans ce schéma (le champ
--                    "active" de la demande initiale est renommé pour rester
--                    cohérent avec meditation_catalog/relaxation_video_catalog).
--       audio_url    URL HTTPS du MP3 pré-généré sur R2. NULLABLE : tant
--                    qu'aucun MP3 n'est généré/téléversé pour une phrase,
--                    l'app utilise un repli TextToSpeech local (jamais
--                    d'appel TTS payant au moment où le réveil sonne).
--     Endpoint : GET /api/app/content/wake-messages (lecture seule,
--     authentifié comme les autres endpoints de contenu, ETag / If-None-
--     Match -> 304). Le système fonctionne AVEC ZÉRO ligne (l'app garde son
--     dernier cache local) et supporte l'ajout de centaines de phrases sans
--     nouvelle version de l'application.
--
-- Idempotence init_db() : CREATE TABLE / INDEX IF NOT EXISTS = no-op au
-- rejeu ; le seed utilise ON CONFLICT (text) DO NOTHING.

CREATE TABLE IF NOT EXISTS wake_messages (
    id           UUID         PRIMARY KEY,
    text         TEXT         NOT NULL UNIQUE,
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    audio_url    TEXT,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wake_messages_active
ON wake_messages (is_active, id);
