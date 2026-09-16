-- 035_meditation_video_catalog.sql — MP4 Méditations R2.
-- Catalogue distinct des audios et des vidéos du Réveil. Additif et rejouable.

CREATE TABLE IF NOT EXISTS meditation_video_catalog (
    id               UUID         PRIMARY KEY,
    slug             TEXT         NOT NULL UNIQUE,
    title            TEXT         NOT NULL,
    description      TEXT         NOT NULL DEFAULT '',
    category         TEXT         NOT NULL DEFAULT '',
    duration_seconds INTEGER,
    video_url        TEXT         NOT NULL,
    thumbnail_url    TEXT,
    sort_order       INTEGER      NOT NULL DEFAULT 0,
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
    published_at     TIMESTAMPTZ,
    r2_object_key    TEXT         UNIQUE,
    r2_last_seen_at  TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    version          INTEGER      NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_meditation_video_catalog_active
ON meditation_video_catalog (is_active, sort_order, id);
