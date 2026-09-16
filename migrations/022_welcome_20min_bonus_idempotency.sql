-- 022_welcome_20min_bonus_idempotency.sql — Migration v48 : GROS CHANTIER
-- ÉCONOMIQUE (Prompt 1/5) — bienvenue 20 min, idempotency_key du bonus.
--
-- Miroir LISIBLE du bloc « Migration v48 » exécuté réellement par init_db()
-- dans auryel_bot.py. PUREMENT ADDITIF : 1 ALTER COLUMN (SET DEFAULT, ne
-- touche aucune ligne existante par lui-même), 1 UPDATE ciblé et NON
-- DESTRUCTEUR, 1 colonne nullable + 1 index UNIQUE PARTIEL. Aucun DROP /
-- TRUNCATE / DELETE, aucune autre table touchée.
--
--   accounts.first_free_seconds_remaining
--     Nouveau standard produit : bienvenue = 20 min (1200 s) au lieu d'1 h
--     (3600 s). `SET DEFAULT 1200` -> tout NOUVEAU compte (INSERT omettant
--     la colonne) reçoit 1200 désormais.
--
--     Migration des comptes EXISTANTS (choix documenté, CRITIQUE — jamais
--     un double cadeau) :
--       - JAMAIS consommée (first_consultation_used_at IS NULL) ET encore
--         EXACTEMENT à l'ancienne valeur par défaut (3600, donc jamais
--         touchée par un ajustement admin ou un débit partiel) -> ramenée
--         à 1200. La bienvenue n'a par définition RIEN coûté à ce compte
--         (aucune seconde consommée) : aligner sur le nouveau standard
--         n'enlève aucun bénéfice déjà exercé. Ce n'est jamais un AJOUT,
--         seulement un RÉALIGNEMENT d'un solde encore vierge.
--       - déjà partiellement/totalement consommée (first_consultation_
--         used_at renseigné), OU à une valeur custom (ex. ajustement admin)
--         -> JAMAIS touchée : stratégie la plus conservatrice face à
--         l'ambiguïté (règle du lot : « si ambigu, choisir l'option la plus
--         sûre économiquement »).
--     Idempotent : au rejeu, plus aucune ligne ne vaut exactement 3600 avec
--     used_at NULL (déjà ramenée à 1200 la 1re fois) -> 0 ligne modifiée.
--
--   time_ledger.idempotency_key
--     Nouvelle colonne nullable + index UNIQUE PARTIEL
--     (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL. Sert
--     UNIQUEMENT à la nouvelle primitive générique `credit_bonus_time` /
--     `_credit_bonus_time_tx` (fondation pour un futur chantier — aucun
--     appelant HTTP dans ce lot) : les lignes historiques (débits de
--     consultation, seed backfill v42…) gardent idempotency_key NULL,
--     jamais concernées par l'index (partiel). Aucune ligne existante
--     modifiée.
--
-- Idempotence init_db() : ADD COLUMN / CREATE INDEX IF NOT EXISTS = no-op
-- au rejeu ; le SET DEFAULT reposé est toujours un no-op sémantique.

ALTER TABLE accounts
ALTER COLUMN first_free_seconds_remaining SET DEFAULT 1200;

UPDATE accounts SET first_free_seconds_remaining = 1200
WHERE first_consultation_used_at IS NULL
AND first_free_seconds_remaining = 3600;

ALTER TABLE time_ledger
ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_time_ledger_user_idempotency
ON time_ledger (user_id, idempotency_key)
WHERE idempotency_key IS NOT NULL;
