-- 007_tirages.sql — Migration v33 (T3) : stockage des tirages 3 cartes mobile.
--
-- Miroir LISIBLE du bloc « Migration v33 » exécuté réellement par init_db()
-- dans auryel_bot.py. Purement additif : une table neuve, un index, deux FK
-- idempotentes. Aucun DROP / TRUNCATE / backfill.
--
-- card_keys : JSONB = liste ordonnée de 3 slugs d'arcane majeur
--   (ex. ["le_fou","la_lune","le_soleil"]). L'ORDRE est celui choisi par
--   l'utilisateur dans l'application ; il n'est jamais réordonné.
-- consultation_id : NULL tant que le tirage n'a pas été discuté ; renseigné
--   une seule fois (premier rattachement conservé).

CREATE TABLE IF NOT EXISTS tirages (
    id              UUID         PRIMARY KEY,
    user_id         UUID         NOT NULL,
    card_keys       JSONB        NOT NULL,
    advisor_id      TEXT         NULL,
    consultation_id UUID         NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tirages_user_created
    ON tirages (user_id, created_at DESC);

-- FK idempotentes (style identique à fk_allowance_source_subscription, v32).
DO $$
BEGIN
    ALTER TABLE tirages
        ADD CONSTRAINT fk_tirages_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    ALTER TABLE tirages
        ADD CONSTRAINT fk_tirages_consultation
        FOREIGN KEY (consultation_id) REFERENCES consultations(id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
