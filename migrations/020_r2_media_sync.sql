-- 020_r2_media_sync.sql — Migration v46 : SYNCHRONISATION AUTOMATIQUE
-- CLOUDFLARE R2 -> catalogues média. Pilotée par un cron (toutes les ~15 min) :
-- déposer un fichier dans R2 suffit, il apparaît dans l'app sans release.
--
-- Miroir LISIBLE du bloc « Migration v46 » exécuté réellement par init_db()
-- dans auryel_bot.py. PUREMENT ADDITIF : 2 colonnes techniques par catalogue
-- média + 1 table d'état. Aucune colonne existante ALTER-ée en place, aucun
-- backfill, aucun DROP / TRUNCATE / DELETE, aucune FK vers accounts.
--
--   <catalogue>.r2_object_key
--     Clé S3 de l'objet R2 source, ex. « méditations/51-retrouver-le-calme.mp3 »
--     ou « relaxation-videos/ocean.mp4 ». NULLABLE : les lignes créées à la
--     main via l'admin restent NULL et ne sont jamais gérées par le sync.
--     INDEX UNIQUE PARTIEL (WHERE r2_object_key IS NOT NULL) = garantie DB
--     anti-doublon : un même fichier R2 ne peut produire qu'UNE seule ligne,
--     même si deux exécutions du cron se chevauchent.
--
--   <catalogue>.r2_last_seen_at
--     Horodatage de la dernière exécution du sync ayant vu cet objet dans R2.
--     Un objet retiré de R2 n'est JAMAIS supprimé de la base : il devient
--     simplement « manquant » (signalé dans le rapport / l'état de sync).
--
--   r2_sync_state
--     Ligne unique (id = 1) : résumé non sensible du dernier passage du sync,
--     lu par GET /admin/content/r2-sync-status (admin only). Ne contient
--     JAMAIS de credential R2.
--
-- Idempotence init_db() : ADD COLUMN / CREATE INDEX / CREATE TABLE IF NOT
-- EXISTS = no-op au rejeu.

ALTER TABLE meditation_catalog        ADD COLUMN IF NOT EXISTS r2_object_key   TEXT;
ALTER TABLE meditation_catalog        ADD COLUMN IF NOT EXISTS r2_last_seen_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS uq_meditation_catalog_r2_object_key
ON meditation_catalog (r2_object_key) WHERE r2_object_key IS NOT NULL;

ALTER TABLE relaxation_video_catalog  ADD COLUMN IF NOT EXISTS r2_object_key   TEXT;
ALTER TABLE relaxation_video_catalog  ADD COLUMN IF NOT EXISTS r2_last_seen_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS uq_relaxation_video_catalog_r2_object_key
ON relaxation_video_catalog (r2_object_key) WHERE r2_object_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS r2_sync_state (
    id              INTEGER      PRIMARY KEY DEFAULT 1,
    last_run_at     TIMESTAMPTZ,
    status          TEXT,
    audio_objects   INTEGER      NOT NULL DEFAULT 0,
    video_objects   INTEGER      NOT NULL DEFAULT 0,
    new_audio       INTEGER      NOT NULL DEFAULT 0,
    new_videos      INTEGER      NOT NULL DEFAULT 0,
    adopted_audio   INTEGER      NOT NULL DEFAULT 0,
    adopted_videos  INTEGER      NOT NULL DEFAULT 0,
    updated_audio   INTEGER      NOT NULL DEFAULT 0,
    updated_videos  INTEGER      NOT NULL DEFAULT 0,
    missing_objects INTEGER      NOT NULL DEFAULT 0,
    invalid_objects INTEGER      NOT NULL DEFAULT 0,
    errors          INTEGER      NOT NULL DEFAULT 0,
    detail          TEXT,
    CONSTRAINT r2_sync_state_singleton CHECK (id = 1)
);
