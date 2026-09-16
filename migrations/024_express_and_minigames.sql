-- 024_express_and_minigames.sql — Migration v50 : GROS CHANTIER AURYEL
-- (Prompt 3/5) — CONSULTATION EXPRESS (Étoiles -> temps) + MINI-JEUX
-- (session serveur générique).
--
-- Miroir LISIBLE du bloc « Migration v50 » exécuté réellement par init_db()
-- dans auryel_bot.py. PUREMENT ADDITIF : 3 tables neuves + 1 colonne
-- additive sur memory_games + 2 FK idempotentes + 1 UPDATE ciblé et GARDÉ
-- (active la règle `mini_game_completed`, créée désactivée en v49 — jamais
-- un écrasement d'un réglage déjà personnalisé). Aucune colonne existante
-- ALTER-ée destructivement, aucun DROP / TRUNCATE / DELETE.
--
--   express_products
--     Configuration SERVEUR (comme reward_rules) du catalogue "temps contre
--     Étoiles" — `stars_cost` / `seconds_granted` / `enabled` jamais codés
--     en dur côté Flutter. Seed idempotent (ON CONFLICT DO NOTHING) :
--     `express_consultation_10min` (500 Étoiles -> 600 s).
--
--   express_consultations
--     Trace d'audit APPEND-ONLY d'un achat réussi (Étoiles dépensées <->
--     temps accordé). UNIQUE (user_id, idempotency_key) : sert de PREMIER
--     gardien d'idempotence (avant même le débit/crédit) — un rejeu de la
--     MÊME clé renvoie le résultat déjà enregistré, ne rejoue jamais le
--     débit/crédit.
--
--   mini_game_sessions
--     Session de jeu SERVEUR générique, partagée par les 2 NOUVEAUX
--     mini-jeux (Suite intuitive / Carte cachée) — même idiome que
--     memory_games (déjà existante, RÉUTILISÉE telle quelle pour le
--     Memory) : `start` crée une session imprévisible, `finish` la ferme
--     EXACTLY-ONCE.
--
--   memory_games.stars_awarded (colonne ADDITIVE)
--     Memory ne crédite plus `earned_seconds_remaining` (ancienne colonne
--     `reward_seconds`, toujours présente, toujours écrite à 0 pour les
--     NOUVELLES parties — jamais supprimée). La récompense réelle
--     (Étoiles, règle `mini_game_completed`, plafond quotidien PARTAGÉ par
--     toute la catégorie mini-jeux) est désormais tracée ici.
--
-- RÉCOMPENSE MINI-JEUX — UNIFIÉE : Memory ET les 2 nouveaux jeux créditent
-- tous la MÊME règle `mini_game_completed` via `award_stars`, dont le
-- plafond quotidien (`daily_action_claims`, partagé par CLÉ DE RÈGLE, pas
-- par jeu) garantit "maximum UNE récompense mini-jeu par jour pour
-- l'ensemble de la catégorie" sans code supplémentaire. `memory_rewards`
-- (v39) cesse de recevoir de nouvelles lignes — son historique existant
-- n'est PAS touché : plus jamais de double récompense temps + Étoiles.
--
-- FK ... -> accounts(user_id) SANS ON DELETE CASCADE (idiome v27) : DELETE
-- /api/app/account purge explicitement express_consultations /
-- mini_game_sessions (cf. _ACCOUNT_DELETE_CHILD_TABLES). `express_products`
-- est une config GLOBALE (comme reward_rules) : aucune FK utilisateur.
--
-- Idempotence init_db() : CREATE TABLE / INDEX / ADD COLUMN IF NOT EXISTS =
-- no-op au rejeu ; les seeds utilisent ON CONFLICT DO NOTHING ; l'UPDATE
-- d'activation est gardé par `AND enabled=FALSE`.

CREATE TABLE IF NOT EXISTS express_products (
    product_key      TEXT         PRIMARY KEY,
    stars_cost       INTEGER      NOT NULL,
    seconds_granted  INTEGER      NOT NULL,
    enabled          BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS express_consultations (
    id               UUID         PRIMARY KEY,
    user_id          UUID         NOT NULL,
    stars_spent      INTEGER      NOT NULL,
    seconds_granted  INTEGER      NOT NULL,
    status           TEXT         NOT NULL DEFAULT 'completed',
    idempotency_key  TEXT         NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_express_consultations_user_idempotency
ON express_consultations (user_id, idempotency_key);

CREATE TABLE IF NOT EXISTS mini_game_sessions (
    id            UUID         PRIMARY KEY,
    user_id       UUID         NOT NULL,
    game_key      TEXT         NOT NULL,
    started_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    status        TEXT         NOT NULL DEFAULT 'active',
    completed_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mini_game_sessions_user_status
ON mini_game_sessions (user_id, status);

ALTER TABLE memory_games
ADD COLUMN IF NOT EXISTS stars_awarded INTEGER NOT NULL DEFAULT 0;

-- FK, blocs idempotents (n'attrapent que duplicate_object).
DO $$
BEGIN
    ALTER TABLE express_consultations
        ADD CONSTRAINT fk_express_consultations_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    ALTER TABLE mini_game_sessions
        ADD CONSTRAINT fk_mini_game_sessions_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

-- Seed idempotent — catalogue express.
INSERT INTO express_products (product_key, stars_cost, seconds_granted, enabled)
VALUES ('express_consultation_10min', 500, 600, TRUE)
ON CONFLICT (product_key) DO NOTHING;

-- Active mini_game_completed (créée désactivée en v49). Gardé par
-- `AND enabled=FALSE` : un opérateur ayant déjà personnalisé/activé cette
-- règle n'est JAMAIS écrasé par un rejeu de cette migration.
UPDATE reward_rules
SET stars_amount=15, enabled=TRUE, daily_limit=1, updated_at=NOW()
WHERE rule_key='mini_game_completed' AND enabled=FALSE;
