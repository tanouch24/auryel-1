-- 034_tirages_daily_idempotency.sql — un tirage quotidien par compte.
-- Additif : les anciennes lignes restent lisibles ; draw_date est renseigné
-- pour les nouvelles écritures. Les anciennes lignes NULL sont traitées par
-- la compatibilité applicative sur created_at.

ALTER TABLE tirages
    ADD COLUMN IF NOT EXISTS draw_date DATE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_tirages_user_draw_date
    ON tirages (user_id, draw_date)
    WHERE draw_date IS NOT NULL;
