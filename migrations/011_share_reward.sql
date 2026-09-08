-- 011_share_reward.sql — Migration v37 (J5) : récompense « 30 jours de partage
-- = 1 heure de consultation offerte », décidée et créditée UNIQUEMENT par le
-- serveur.
--
-- Miroir LISIBLE du bloc « Migration v37 » exécuté réellement par init_db()
-- dans auryel_bot.py. PUREMENT ADDITIF : une table neuve + une colonne
-- nullable sur `accounts` + une FK idempotente. Aucune colonne existante
-- ALTER-ée, aucun backfill, aucun DROP / TRUNCATE / DELETE, aucune donnée
-- existante modifiée.
--
-- Règle produit figée :
--   * 1 jour calendaire (Europe/Paris) comptabilisé au maximum par jour ;
--   * 30 jours DISTINCTS comptabilisés = 1 h (3600 s) offerte ;
--   * UNE SEULE récompense par compte, à vie (count plafonné à 30 ensuite,
--     aucun 2e crédit, aucun nouveau cycle).
--
-- Le client déclare seulement une action de partage volontaire ; il ne prouve
-- jamais qu'une publication a eu lieu sur un réseau social.
--
--   share_reward_days
--     Un jour de partage déclaré. PRIMARY KEY (user_id, share_date) :
--     l'unicité « 1 jour comptabilisé max / jour » est garantie EN BASE. Les
--     routes utilisent `INSERT ... ON CONFLICT (user_id, share_date) DO
--     NOTHING` -> POST concurrents / rejeux d'un même jour = 1 seul jour.
--
--   accounts.share_reward_credited_at   TIMESTAMPTZ NULL
--     Marqueur du crédit UNIQUE par compte. Le crédit passe par
--     `UPDATE accounts SET purchased_seconds_remaining = COALESCE(...,0) + 3600,
--      share_reward_credited_at = now
--      WHERE user_id = %s AND share_reward_credited_at IS NULL`
--     sous `accounts ... FOR UPDATE` -> exactement 1 crédit, le retry du 30e
--     jour renvoie credited=false / credited_seconds=0.
--     Le crédit atterrit dans accounts.purchased_seconds_remaining (bucket
--     « temps acheté / crédité », jamais remis à zéro par un reset mensuel,
--     consommé en dernier — migration v34) et apparaît immédiatement dans
--     GET /api/consultation/state (bloc `time`).
--
-- FK share_reward_days.user_id -> accounts(user_id), SANS ON DELETE CASCADE
-- (cohérent avec le reste du schéma app : DELETE /api/app/account purge
-- explicitement cette table). Bloc DO $$ idempotent (n'attrape que
-- duplicate_object), même idiome que fk_mobile_subscriptions_account (v27).
--
-- Idempotence : CREATE TABLE IF NOT EXISTS + ADD COLUMN IF NOT EXISTS sont des
-- no-op au rejeu de init_db() ; la FK est protégée par le DO $$.

CREATE TABLE IF NOT EXISTS share_reward_days (
    user_id     UUID         NOT NULL,
    share_date  DATE         NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, share_date)
);

ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS share_reward_credited_at TIMESTAMPTZ;

DO $$
BEGIN
    ALTER TABLE share_reward_days
        ADD CONSTRAINT fk_share_reward_days_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
