-- 028_wellbeing_ebook_library.sql — Catalogue d'ebooks Bien-être
-- Additif et idempotent : le programme 30 jours et ses données restent intactes.
CREATE TABLE IF NOT EXISTS wellbeing_ebooks (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    subtitle TEXT NOT NULL,
    description TEXT,
    cover_url TEXT,
    pdf_url TEXT,
    publication_date DATE NOT NULL,
    month_label TEXT,
    version TEXT NOT NULL DEFAULT '1',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    featured BOOLEAN NOT NULL DEFAULT FALSE,
    notification_sent_at TIMESTAMPTZ,
    push_type TEXT NOT NULL DEFAULT 'ebook_monthly',
    push_title TEXT,
    push_body TEXT,
    push_deeplink TEXT NOT NULL DEFAULT 'wellbeing_library',
    push_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wellbeing_ebooks_publication
ON wellbeing_ebooks(active, publication_date DESC);

ALTER TABLE wellbeing_program_ebook_config
    ADD COLUMN IF NOT EXISTS ebook_id INTEGER;

INSERT INTO wellbeing_ebooks
    (id, slug, title, subtitle, description, cover_url, pdf_url,
     publication_date, month_label, version, active, featured,
     push_title, push_body)
VALUES
    (1, '30-jours-pour-prendre-soin-de-soi',
     '30 jours pour prendre soin de soi',
     'Le petit guide Bien-être Auryel',
     'Un guide simple pour installer de petites habitudes de bien-être au quotidien.',
     NULL, NULL, CURRENT_DATE, NULL, '1', TRUE, TRUE,
     'Ton nouvel ebook Auryel est disponible',
     'Découvre gratuitement le nouveau guide Bien-être de ce mois.')
ON CONFLICT (id) DO NOTHING;

UPDATE wellbeing_program_ebook_config
SET ebook_id = 1
WHERE id = 1 AND ebook_id IS NULL;
