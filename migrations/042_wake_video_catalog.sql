-- 042_wake_video_catalog.sql — catalogue dédié des vidéos du Réveil.
-- Les MP4 restent dans R2 ; PostgreSQL ne conserve que les métadonnées.

CREATE TABLE IF NOT EXISTS wake_video_catalog (
    id               UUID         PRIMARY KEY,
    slug             TEXT         NOT NULL UNIQUE,
    title            TEXT         NOT NULL,
    video_url        TEXT         NOT NULL,
    duration_seconds INTEGER,
    sort_order       INTEGER      NOT NULL DEFAULT 0,
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
    published_at     TIMESTAMPTZ,
    r2_object_key    TEXT         UNIQUE,
    r2_last_seen_at  TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    version          INTEGER      NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_wake_video_catalog_active
ON wake_video_catalog (is_active, sort_order, id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_wake_video_catalog_r2_object_key
ON wake_video_catalog (r2_object_key)
WHERE r2_object_key IS NOT NULL;
