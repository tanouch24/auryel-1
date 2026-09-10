-- 019_relaxation_videos.sql — Migration v45 : VIDÉOS APAISANTES DISTANTES,
-- ambiance visuelle (muette) jouée en fond d'une méditation, pilotable depuis
-- l'admin SANS mise à jour de l'application.
--
-- Miroir LISIBLE du bloc « Migration v45 » exécuté réellement par init_db()
-- dans auryel_bot.py. PUREMENT ADDITIF : 1 table neuve + 1 index. Aucune
-- colonne existante ALTER-ée, aucun backfill, aucun DROP / TRUNCATE / DELETE.
-- Aucune FK vers accounts : le contenu est GLOBAL (même pour tous), donc
-- absent de _ACCOUNT_DELETE_CHILD_TABLES.
--
--   relaxation_video_catalog
--     Catalogue distant des vidéos d'ambiance. Même philosophie que
--     meditation_catalog (v43) :
--       slug           clé stable UNIQUE (upsert admin)
--       video_url      URL HTTPS bornée + validée (_validate_media_url) ;
--       thumbnail_url  idem, facultative. AUCUN upload binaire dans ce lot :
--                      l'admin colle une URL déjà hébergée ailleurs.
--       category       TEXTE LIBRE (ocean, rain, forest, night, calm, …),
--                      jamais un enum fermé. Défaut 'calm' = générique
--                      (compatible avec toutes les méditations).
--       tags           TEXT[] libre (mots-clés d'ambiance).
--       is_active      interrupteur (masque sans supprimer)
--       published_at   NULL = publié ; futur = programmé
--       sort_order     réordonnancement -> tri déterministe (sort_order, id)
--       version        incrémentée à chaque édition -> invalidation du cache
--                      client (ETag / catalog_version).
--     Endpoint : GET /api/app/content/relaxation-videos (ETag / If-None-Match
--     -> 304). La compatibilité méditation <-> vidéo est calculée à la lecture
--     par la config backend _MEDITATION_VIDEO_COMPATIBILITY (pas stockée) :
--     changer le mapping ne nécessite aucune migration. Le système fonctionne
--     AVEC ZÉRO vidéo (l'app retombe sur un fond statique).
--
-- Idempotence init_db() : CREATE TABLE / INDEX IF NOT EXISTS = no-op au rejeu.

CREATE TABLE IF NOT EXISTS relaxation_video_catalog (
    id               UUID         PRIMARY KEY,
    slug             TEXT         NOT NULL UNIQUE,
    title            TEXT         NOT NULL,
    description      TEXT,
    video_url        TEXT         NOT NULL,
    thumbnail_url    TEXT,
    category         TEXT         NOT NULL DEFAULT 'calm',
    tags             TEXT[]       NOT NULL DEFAULT '{}',
    sort_order       INTEGER      NOT NULL DEFAULT 0,
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
    published_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    version          INTEGER      NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_relaxation_video_catalog_active
ON relaxation_video_catalog (is_active, sort_order, id);
