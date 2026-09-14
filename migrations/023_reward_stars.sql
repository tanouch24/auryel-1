-- 023_reward_stars.sql — Migration v49 : GROS CHANTIER AURYEL (Prompt 2/5) —
-- ÉTOILES AURYEL, monnaie interne virtuelle (non transférable, sans valeur
-- monétaire, non remboursable).
--
-- Miroir LISIBLE du bloc « Migration v49 » exécuté réellement par init_db()
-- dans auryel_bot.py. PUREMENT ADDITIF : 4 tables neuves + 3 FK idempotentes
-- + un seed idempotent de règles. Aucune colonne existante ALTER-ée, aucun
-- backfill, aucun DROP / TRUNCATE / DELETE, aucune donnée existante modifiée.
-- NE RÉUTILISE PAS le moteur temps (accounts.*_seconds_remaining,
-- time_ledger) : les Étoiles sont une monnaie DISTINCTE, avec son propre
-- wallet/journal/règles — l'audit du rapport de ce lot a confirmé qu'aucune
-- architecture de « points » ne préexistait (share_reward_days /
-- wellbeing_mission_days / memory_rewards créditent tous du TEMPS, jamais
-- une monnaie de points).
--
--   reward_wallet
--     UNE ligne par utilisateur. `stars_balance` = solde ACTUEL (source de
--     vérité affichée). `current_streak` / `best_streak` /
--     `last_active_reward_date` : streak de jours actifs consécutifs (jour
--     Europe/Paris, même convention que `_wellbeing_day` /
--     `_reward_share_date` — pas une 3e fonction de jour). Créée
--     paresseusement (INSERT ... ON CONFLICT DO NOTHING) au premier
--     `award_stars` : un compte qui ne gagne jamais d'Étoile n'a pas besoin
--     de ligne.
--
--   reward_rules
--     Configuration SERVEUR des montants (jamais codés en dur dans
--     Flutter). `daily_limit` : NULL = pas de plafond quotidien dédié (ex.
--     `streak_7_days`, jalon et non action quotidienne) ; 1 = la seule
--     valeur exploitée dans ce lot (toutes les actions quotidiennes),
--     appliquée via l'UNIQUE de `daily_action_claims` (pas de support N>1/jour
--     dans ce lot — non nécessaire aujourd'hui). `cooldown_seconds` : NULL =
--     non utilisé pour l'instant (colonne prête pour une future règle à
--     cooldown). Seed idempotent (ON CONFLICT (rule_key) DO NOTHING) : un
--     montant ajusté ensuite en base par un opérateur n'est JAMAIS écrasé
--     par un rejeu de migration. `mini_game_completed` / `rewarded_ad_
--     completed` : clés FUTURES prévues mais `enabled=FALSE` — aucun
--     appelant dans ce lot (interdit : mini-jeux, AdMob).
--
--   daily_action_claims
--     Anti-farming EN BASE (pas seulement applicatif) : UNIQUE
--     (user_id, action_key, claim_date) — fermer/réouvrir l'app, logout/
--     login, spam bouton, retry réseau, multi-device ne peuvent jamais
--     produire une 2e ligne pour le même (utilisateur, action, jour). Sert
--     aussi de base à la détection de streak (`_bump_streak_tx`, appelée
--     depuis `award_stars`).
--
--   reward_transactions
--     Journal APPEND-ONLY (aucun UPDATE destructif d'historique). `type` :
--     'earn' (seul type produit dans ce lot) ou 'spend' (fondation pour un
--     futur débit — `_debit_stars_tx` existe déjà côté serveur, non exposé
--     en HTTP). `idempotency_key` + index UNIQUE PARTIEL
--     (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL : MÊME
--     idiome que `time_ledger.idempotency_key` (migration v48) — protection
--     supplémentaire au-delà de `daily_action_claims`, notamment pour les
--     jalons non quotidiens (`streak_7_days`).
--
-- FK ... -> accounts(user_id) SANS ON DELETE CASCADE (idiome v27 : blocs
-- DO $$ n'attrapant que duplicate_object) : DELETE /api/app/account purge
-- explicitement reward_wallet / reward_transactions / daily_action_claims
-- (cf. _ACCOUNT_DELETE_CHILD_TABLES). `reward_rules` est une table de
-- configuration GLOBALE (comme wake_messages) : aucune FK utilisateur,
-- absente de _ACCOUNT_DELETE_CHILD_TABLES.
--
-- Idempotence init_db() : CREATE TABLE / INDEX IF NOT EXISTS = no-op au
-- rejeu ; le seed utilise ON CONFLICT (rule_key) DO NOTHING.

CREATE TABLE IF NOT EXISTS reward_wallet (
    user_id                  UUID         PRIMARY KEY,
    stars_balance            BIGINT       NOT NULL DEFAULT 0,
    current_streak           INTEGER      NOT NULL DEFAULT 0,
    best_streak              INTEGER      NOT NULL DEFAULT 0,
    last_active_reward_date  DATE,
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS reward_rules (
    rule_key         TEXT         PRIMARY KEY,
    stars_amount     INTEGER      NOT NULL,
    enabled          BOOLEAN      NOT NULL DEFAULT TRUE,
    daily_limit      INTEGER,
    cooldown_seconds INTEGER,
    metadata         JSONB,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS daily_action_claims (
    id             UUID         PRIMARY KEY,
    user_id        UUID         NOT NULL,
    action_key     TEXT         NOT NULL,
    claim_date     DATE         NOT NULL,
    source_id      TEXT,
    stars_awarded  INTEGER      NOT NULL,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_action_claims_user_action_date
ON daily_action_claims (user_id, action_key, claim_date);

CREATE TABLE IF NOT EXISTS reward_transactions (
    id               UUID         PRIMARY KEY,
    user_id          UUID         NOT NULL,
    delta_stars      INTEGER      NOT NULL,
    balance_after    BIGINT       NOT NULL,
    type             TEXT         NOT NULL,
    reason           TEXT         NOT NULL,
    source_type      TEXT,
    source_id        TEXT,
    idempotency_key  TEXT,
    metadata         JSONB,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_reward_transactions_user_idempotency
ON reward_transactions (user_id, idempotency_key)
WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_reward_transactions_user_created
ON reward_transactions (user_id, created_at DESC);

-- FK, blocs idempotents (n'attrapent que duplicate_object).
DO $$
BEGIN
    ALTER TABLE reward_wallet
        ADD CONSTRAINT fk_reward_wallet_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    ALTER TABLE daily_action_claims
        ADD CONSTRAINT fk_daily_action_claims_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    ALTER TABLE reward_transactions
        ADD CONSTRAINT fk_reward_transactions_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

-- Seed idempotent — montants du rapport (§3). ON CONFLICT DO NOTHING
-- uniquement : un montant ajusté ensuite en base par un opérateur n'est
-- JAMAIS écrasé par un rejeu de cette migration.
INSERT INTO reward_rules (rule_key, stars_amount, enabled, daily_limit, cooldown_seconds)
VALUES
    ('wake_completed',        5,  TRUE,  1,    NULL),
    ('daily_card_completed',  10, TRUE,  1,    NULL),
    ('tarot_completed',       10, TRUE,  1,    NULL),
    ('meditation_completed',  10, TRUE,  1,    NULL),
    ('share_completed',       15, TRUE,  1,    NULL),
    ('streak_7_days',         50, TRUE,  NULL, NULL),
    ('mini_game_completed',   0,  FALSE, NULL, NULL),
    ('rewarded_ad_completed', 0,  FALSE, NULL, NULL)
ON CONFLICT (rule_key) DO NOTHING;
