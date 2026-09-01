-- 008_time_seconds.sql — Migration v34 (TIMER-A.1) : SCHÉMA du futur compteur
-- de temps (secondes consommées) pour l'offre « 8 h Premium / mois + 1 h
-- offerte + heures achetées ».
--
-- Miroir LISIBLE du bloc « Migration v34 » exécuté réellement par init_db()
-- dans auryel_bot.py. PUREMENT ADDITIF : 6 colonnes nullables sur 3 tables
-- existantes + UN backfill idempotent guardé. Aucun DROP / TRUNCATE / DELETE,
-- aucune colonne existante ALTER-ée.
--
-- IMPORTANT — à ce stade AUCUN moteur, AUCUN endpoint, AUCUNE fonction de
-- consultation ne lit ni n'écrit ces colonnes. Le modèle par-compte
-- (consultation_allowance.monthly_limit / monthly_used,
--  consultations.expires_at = started_at + 2 h) reste la SEULE logique active.
-- Ce lot prépare uniquement la structure ; le câblage est un lot ultérieur.
--
--   accounts.first_free_seconds_remaining
--     « 1 h offerte », en secondes. D'abord ajoutée SANS DEFAULT pour que le
--     backfill par CASE puisse distinguer NULL (à remplir) d'une valeur déjà
--     posée :
--       3600 si first_consultation_used_at IS NULL (gratuite jamais consommée)
--       0    sinon.
--     Le DEFAULT 3600 est ensuite posé (ALTER COLUMN ... SET DEFAULT), APRÈS le
--     backfill : un nouveau compte hérite de 3600 dès l'INSERT, sans réécrire
--     les comptes existants déjà mis à 0 par le CASE.
--     first_consultation_used_at est CONSERVÉE (analytics + rétro-compat de
--     first_free_available).
--
--   accounts.purchased_seconds_remaining
--     Temps acheté cumulé (heures supplémentaires). DEFAULT 0 ; le paiement
--     consommable Store n'est PAS dans ce lot, la colonne reste à 0 partout.
--     Jamais remise à zéro par un futur reset mensuel.
--
--   consultation_allowance.monthly_allowance_seconds
--     « 8 h » Premium par période. DEFAULT 28800 : les lignes existantes
--     lisent 28800 sans UPDATE. Distincte de monthly_limit (nombre de
--     consultations), qui reste inchangée et active.
--
--   consultation_allowance.monthly_used_seconds
--     Secondes Premium consommées sur la période. DEFAULT 0. Nouveau compteur,
--     distinct de monthly_used (nombre de consultations), qui reste inchangé
--     et actif tant que le moteur n'est pas câblé.
--
--   consultations.last_activity_at / billed_until
--     TIMESTAMPTZ NULL. Futurs curseurs de la fenêtre d'activité de 5 min.
--     NULL partout : aucune consultation n'a de temps facturé.
--
-- Idempotence : chaque ADD COLUMN IF NOT EXISTS est un no-op au rejeu ; le
-- backfill first_free porte WHERE first_free_seconds_remaining IS NULL -> au 2e
-- passage 0 ligne touchée, et une valeur déjà posée n'est jamais réécrite.
-- L'ALTER COLUMN ... SET DEFAULT 3600 est aussi idempotent (repose le même
-- défaut, jamais d'erreur) et ne réécrit pas les lignes existantes. Grâce au
-- DEFAULT, un compte créé après la migration a directement 3600 ; le
-- WHERE ... IS NULL ne sert plus que de filet (ligne insérée pendant la fenêtre
-- où le DEFAULT n'était pas encore posé), régularisée au prochain init_db().

ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS first_free_seconds_remaining INTEGER;

ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS purchased_seconds_remaining INTEGER DEFAULT 0;

UPDATE accounts
    SET first_free_seconds_remaining =
        CASE WHEN first_consultation_used_at IS NULL THEN 3600 ELSE 0 END
    WHERE first_free_seconds_remaining IS NULL;

-- DEFAULT posé APRÈS le backfill (voir en-tête).
ALTER TABLE accounts
    ALTER COLUMN first_free_seconds_remaining SET DEFAULT 3600;

ALTER TABLE consultation_allowance
    ADD COLUMN IF NOT EXISTS monthly_allowance_seconds INTEGER DEFAULT 28800;

ALTER TABLE consultation_allowance
    ADD COLUMN IF NOT EXISTS monthly_used_seconds INTEGER DEFAULT 0;

ALTER TABLE consultations
    ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ;

ALTER TABLE consultations
    ADD COLUMN IF NOT EXISTS billed_until TIMESTAMPTZ;
