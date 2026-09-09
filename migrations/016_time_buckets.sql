-- 016_time_buckets.sql — Migration v42 : SÉPARATION DES BUCKETS DE TEMPS.
--
-- Miroir LISIBLE du bloc « Migration v42 » exécuté réellement par init_db()
-- dans auryel_bot.py. PUREMENT ADDITIF : 1 colonne + 2 tables neuves + 1 FK +
-- 1 backfill NON REJOUABLE. Aucune colonne existante ALTER-ée hors ajout,
-- aucun DROP / TRUNCATE / DELETE. resync_premium_entitlement N'EST PAS touché.
--
--   accounts.earned_seconds_remaining
--     Nouveau bucket « minutes GAGNÉES » : reçoit désormais TOUTES les
--     récompenses (partage 30 jours, cycle bien-être, jeu Memory). Distinct de
--     accounts.purchased_seconds_remaining, qui restera pour les futures HEURES
--     RÉELLEMENT ACHETÉES (consommable `auryel_extra_1h`, lot suivant).
--     Ordre de débit du moteur temps :
--       first_free -> premium -> earned -> purchased
--     DEFAULT 0 : un compte créé après la migration a directement 0.
--
--   time_ledger
--     Journal d'audit APPEND-ONLY des mouvements de temps.
--       bucket        ∈ ('first_free','premium','earned','purchased')
--       delta_seconds > 0 crédit, < 0 débit
--       reason        libellé métier ('reward_share', 'reward_wellbeing_cycle',
--                     'reward_memory_game', 'consultation_debit',
--                     'backfill_v42_purchased_to_earned', ...)
--       ref_id        référence facultative (consultation_id, game_id, cycle...)
--     JAMAIS relu comme source de vérité, JAMAIS alimenté par une valeur venue
--     du client : les soldes font foi sur accounts / consultation_allowance.
--     Écrit dans la MÊME transaction que le mouvement qu'il trace (atomicité
--     inchangée). FK -> accounts(user_id) SANS ON DELETE CASCADE : purge
--     explicite au DELETE /api/app/account (_ACCOUNT_DELETE_CHILD_TABLES).
--
--   schema_backfills
--     Marqueur générique « backfill déjà appliqué » (name PRIMARY KEY). Rend le
--     déplacement purchased -> earned STRICTEMENT non rejouable.
--
--   BACKFILL v42_purchased_rewards_to_earned
--     HYPOTHÈSE VÉRIFIÉE DANS LE CODE : les seules écritures de
--     accounts.purchased_seconds_remaining sont
--       (1) le débit du moteur temps (_debit_consultation_seconds_tx) ;
--       (2) TROIS crédits de récompense : partage (api_rewards_daily_share),
--           cycle bien-être (_reconcile_wellbeing_progress), jeu Memory
--           (api_memory_complete).
--     AUCUN achat mobile : le consommable `auryel_consultation_extra` /
--     `auryel_extra_1h` n'est pas implémenté. Donc tout le stock actuel de
--     purchased_seconds_remaining provient des récompenses -> il est déplacé
--     UNE fois vers earned_seconds_remaining, purchased est remis à 0, chaque
--     mouvement est tracé au ledger, puis le marqueur est posé. Au rejeu
--     d'init_db() : marqueur présent -> aucun déplacement.
--     Si l'hypothèse n'avait pas été démontrable, le backfill aurait été
--     ANNULÉ et signalé, sans destruction de données.

ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS earned_seconds_remaining INTEGER DEFAULT 0;

CREATE TABLE IF NOT EXISTS time_ledger (
    id            UUID         PRIMARY KEY,
    user_id       UUID         NOT NULL,
    bucket        TEXT         NOT NULL,
    delta_seconds INTEGER      NOT NULL,
    reason        TEXT         NOT NULL,
    ref_id        TEXT,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_time_ledger_user
ON time_ledger (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS schema_backfills (
    name       TEXT         PRIMARY KEY,
    applied_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

DO $$
BEGIN
    ALTER TABLE time_ledger
        ADD CONSTRAINT fk_time_ledger_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

-- Backfill non rejouable (exécuté par init_db seulement si le marqueur est absent).
-- Équivalent ensembliste du bloc Python (qui, lui, trace aussi le ledger
-- par compte) :
--   UPDATE accounts
--      SET earned_seconds_remaining =
--            COALESCE(earned_seconds_remaining, 0)
--          + COALESCE(purchased_seconds_remaining, 0),
--          purchased_seconds_remaining = 0
--    WHERE COALESCE(purchased_seconds_remaining, 0) <> 0;
--   INSERT INTO schema_backfills (name) VALUES ('v42_purchased_rewards_to_earned');
