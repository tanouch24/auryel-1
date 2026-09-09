-- 017_remote_content.sql — Migration v43 : CONTENU DISTANT (méditations +
-- contenu du jour), pilotable depuis l'admin SANS mise à jour de l'application.
--
-- Miroir LISIBLE du bloc « Migration v43 » exécuté réellement par init_db()
-- dans auryel_bot.py. PUREMENT ADDITIF : 2 tables neuves + index. Aucune
-- colonne existante ALTER-ée, aucun backfill, aucun DROP / TRUNCATE / DELETE.
-- Aucune FK vers accounts : le contenu est GLOBAL (même pour tous), donc
-- absent de _ACCOUNT_DELETE_CHILD_TABLES.
--
--   meditation_catalog
--     Catalogue distant des séances « Ton Moment ». Remplace la liste figée
--     de lib/data/meditation_catalog.dart côté Flutter.
--       slug             clé stable UNIQUE (upsert admin)
--       audio_url        URL HTTPS bornée + validée (_validate_media_url) ;
--       image_url        idem, facultative. AUCUN upload binaire dans ce lot :
--                        l'admin colle une URL déjà hébergée ailleurs.
--       is_active        interrupteur (masque sans supprimer)
--       published_at     NULL = publié ; futur = programmé
--       sort_order       réordonnancement
--       version          incrémentée à chaque édition -> invalidation du
--                        cache client par version
--     Endpoint : GET /api/app/content/meditations (ETag / catalog_version).
--
--   daily_content
--     Deux content_type :
--       'daily_thought'      phrase (text) + explication + visuel partageable
--       'daily_publication'  titre + texte + visuel partageable
--     Contrainte UNIQUE (content_type, publication_date) : au plus un contenu
--     de chaque type par jour. publication_date = jour Europe/Paris, déterminé
--     par le SERVEUR (jamais l'horloge du téléphone).
--     Endpoint : GET /api/app/content/today (date serveur Europe/Paris).
--
-- Idempotence init_db() : CREATE TABLE / INDEX IF NOT EXISTS = no-op au rejeu.

CREATE TABLE IF NOT EXISTS meditation_catalog (
    id               UUID         PRIMARY KEY,
    slug             TEXT         NOT NULL UNIQUE,
    title            TEXT         NOT NULL,
    description      TEXT         NOT NULL DEFAULT '',
    category         TEXT         NOT NULL DEFAULT '',
    duration_seconds INTEGER      NOT NULL DEFAULT 0,
    audio_url        TEXT         NOT NULL,
    image_url        TEXT,
    sort_order       INTEGER      NOT NULL DEFAULT 0,
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
    published_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    version          INTEGER      NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_meditation_catalog_active
ON meditation_catalog (is_active, sort_order, id);

CREATE TABLE IF NOT EXISTS daily_content (
    id               UUID         PRIMARY KEY,
    content_type     TEXT         NOT NULL,
    publication_date DATE         NOT NULL,
    title            TEXT,
    text             TEXT         NOT NULL DEFAULT '',
    explanation      TEXT,
    image_url        TEXT,
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_content_type_date
ON daily_content (content_type, publication_date);

CREATE INDEX IF NOT EXISTS idx_daily_content_lookup
ON daily_content (publication_date, content_type, is_active);
