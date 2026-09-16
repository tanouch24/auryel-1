-- 037_wellbeing_ebook_catalog.sql — catalogue dynamique des ebooks Bien-être.
-- Additif et rejouable : conserve la table et les lignes legacy si elles
-- existent déjà, sans import de contenu ni écriture R2.

CREATE TABLE IF NOT EXISTS wellbeing_ebooks (
    id               INTEGER PRIMARY KEY,
    slug             TEXT NOT NULL UNIQUE,
    title            TEXT NOT NULL,
    subtitle         TEXT NOT NULL DEFAULT '',
    description      TEXT,
    category         TEXT NOT NULL DEFAULT '',
    display_author   TEXT,
    object_key       TEXT,
    cover_key        TEXT,
    cover_url        TEXT,
    pdf_url          TEXT,
    publication_date DATE NOT NULL DEFAULT CURRENT_DATE,
    month_label      TEXT,
    sort_order       INTEGER NOT NULL DEFAULT 0,
    version          TEXT NOT NULL DEFAULT '1',
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    featured         BOOLEAN NOT NULL DEFAULT FALSE,
    notification_sent_at TIMESTAMPTZ,
    push_type        TEXT NOT NULL DEFAULT 'ebook_monthly',
    push_title       TEXT,
    push_body        TEXT,
    push_deeplink    TEXT NOT NULL DEFAULT 'wellbeing_library',
    push_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE wellbeing_ebooks
    ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT '';
ALTER TABLE wellbeing_ebooks
    ADD COLUMN IF NOT EXISTS display_author TEXT;
ALTER TABLE wellbeing_ebooks
    ADD COLUMN IF NOT EXISTS object_key TEXT;
ALTER TABLE wellbeing_ebooks
    ADD COLUMN IF NOT EXISTS cover_key TEXT;
ALTER TABLE wellbeing_ebooks
    ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_wellbeing_ebooks_publication_order
ON wellbeing_ebooks(active, publication_date DESC, sort_order ASC, id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_wellbeing_ebooks_object_key
ON wellbeing_ebooks(object_key)
WHERE object_key IS NOT NULL;
