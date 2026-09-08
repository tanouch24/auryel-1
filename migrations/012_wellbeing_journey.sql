-- 012_wellbeing_journey.sql — Migration v38 (J7) : « Mon parcours bien-être »
-- — missions quotidiennes + récompense par CYCLE de 30 journées COMPLÉTÉES
-- (+15 min = 900 s de consultation), décidée et créditée UNIQUEMENT par le
-- serveur.
--
-- Miroir LISIBLE du bloc « Migration v38 » exécuté réellement par init_db()
-- dans auryel_bot.py. PUREMENT ADDITIF : 2 tables neuves + 2 FK idempotentes.
-- Aucune colonne existante ALTER-ée, aucun backfill, aucun DROP / TRUNCATE /
-- DELETE, aucune donnée existante modifiée.
--
-- Règle produit figée :
--   * 4 missions quotidiennes RÉELLES d'Auryel :
--       pensee       -> partage de la Pensée du jour  (trace : share_reward_days)
--       tirage       -> Carte / tirage du jour         (trace : tirages.created_at)
--       consultation -> échange avec un conseiller     (trace : consultations.last_activity_at)
--       moment       -> séance « Ton Moment » aboutie  (AUCUNE trace serveur -> enregistrée)
--   * 1 JOURNÉE COMPLÉTÉE = les 4 missions accomplies le MÊME jour calendaire
--     Europe/Paris. 3/4 ou moins = aucune journée ajoutée.
--   * Les jours n'ont PAS besoin d'être consécutifs ; un jour manqué ne remet
--     jamais la progression à zéro ; une même date ne compte qu'une fois.
--   * Cycles de 30 journées COMPLÉTÉES (pas 30 jours calendaires). À la 30e :
--     niveau « Rayonnement » + 900 s de consultation, UNE fois par cycle.
--
-- Le SERVEUR est l'unique autorité : aucun compteur client n'est lu ; la
-- progression et la récompense survivent à la fermeture de l'app, à la
-- déconnexion / reconnexion et au changement d'appareil.
--
--   wellbeing_mission_days
--     Une mission SANS trace serveur (aujourd'hui : `moment`) enregistrée pour
--     un jour calendaire Europe/Paris. PRIMARY KEY (user_id, day_date,
--     mission_id) : l'unicité « 1 fois par jour et par mission » est garantie
--     EN BASE. Les routes utilisent
--     `INSERT ... ON CONFLICT (user_id, day_date, mission_id) DO NOTHING`
--     -> rejeux / POST concurrents d'un même jour = 1 seule ligne.
--     Les missions `pensee` / `tirage` / `consultation` NE sont PAS stockées
--     ici : elles sont DÉRIVÉES de leurs traces serveur existantes
--     (share_reward_days.share_date, tirages.created_at,
--     consultations.last_activity_at) — un booléen client n'est jamais accepté.
--
--   wellbeing_cycle_rewards
--     Un cycle de 30 journées complétées récompensé. PRIMARY KEY
--     (user_id, cycle_number) + `INSERT ... ON CONFLICT DO NOTHING` sous
--     `accounts ... FOR UPDATE` -> EXACTEMENT 1 crédit de 900 s par cycle,
--     insensible aux rejeux de requête / réinstallations / changements
--     d'appareil / déconnexions / manipulations d'horloge (les journées
--     dérivent de traces serveur horodatées côté serveur).
--     Le crédit passe par
--     `UPDATE accounts SET purchased_seconds_remaining = COALESCE(...,0) + 900
--      WHERE user_id = %s`
--     après un INSERT dont `rowcount = 1`. Il atterrit dans
--     accounts.purchased_seconds_remaining (bucket « temps acheté / crédité »,
--     jamais remis à zéro par un reset mensuel, consommé en dernier —
--     migration v34) et apparaît immédiatement dans
--     GET /api/consultation/state (bloc `time`).
--
-- FK wellbeing_mission_days.user_id  -> accounts(user_id) SANS ON DELETE CASCADE
-- FK wellbeing_cycle_rewards.user_id -> accounts(user_id) SANS ON DELETE CASCADE
-- (cohérent avec le reste du schéma app : DELETE /api/app/account purge
-- explicitement les 2 tables). Blocs DO $$ idempotents (n'attrapent que
-- duplicate_object) — même idiome que fk_share_reward_days_account (v37).
--
-- Idempotence : CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS sont
-- des no-op au rejeu de init_db() ; les FK sont protégées par les DO $$.

CREATE TABLE IF NOT EXISTS wellbeing_mission_days (
    user_id     UUID         NOT NULL,
    day_date    DATE         NOT NULL,
    mission_id  TEXT         NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, day_date, mission_id)
);

CREATE INDEX IF NOT EXISTS idx_wellbeing_mission_days_user
ON wellbeing_mission_days (user_id, day_date);

CREATE TABLE IF NOT EXISTS wellbeing_cycle_rewards (
    user_id          UUID         NOT NULL,
    cycle_number     INTEGER      NOT NULL,
    credited_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    credited_seconds INTEGER      NOT NULL DEFAULT 900,
    PRIMARY KEY (user_id, cycle_number)
);

DO $$
BEGIN
    ALTER TABLE wellbeing_mission_days
        ADD CONSTRAINT fk_wellbeing_mission_days_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    ALTER TABLE wellbeing_cycle_rewards
        ADD CONSTRAINT fk_wellbeing_cycle_rewards_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
