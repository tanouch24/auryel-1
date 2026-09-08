-- 013_memory_rewards.sql — Migration v39 : JEU MEMORY + RÉCOMPENSES DE TEMPS
-- (« Jeu Auryel »). Le SERVEUR est l'unique autorité : une partie est OUVERTE
-- puis FERMÉE côté serveur ; le chrono = completed_at_serveur -
-- started_at_serveur (jamais lu du client) ; la récompense atterrit dans
-- accounts.purchased_seconds_remaining, décidée et créditée UNIQUEMENT par le
-- serveur.
--
-- Miroir LISIBLE du bloc « Migration v39 » exécuté réellement par init_db()
-- dans auryel_bot.py. PUREMENT ADDITIF : 2 tables neuves + 2 FK idempotentes.
-- Aucune colonne existante ALTER-ée, aucun backfill, aucun DROP / TRUNCATE /
-- DELETE, aucune donnée existante modifiée.
--
-- Règle produit figée — 3 difficultés :
--   easy   : réussite en MOINS DE 20 s -> +300 s (5 min)
--   medium : réussite en MOINS DE 40 s -> +600 s (10 min)
--   hard   : réussite en MOINS DE 80 s -> +900 s (15 min)
--   « moins de » = STRICT (elapsed_seconds >= seuil ne crédite pas).
--
-- Anti-abus V1 : 1 récompense par difficulté sur une FENÊTRE GLISSANTE de 7
-- jours — une difficulté redevient éligible 7 jours (horloge serveur) après SON
-- dernier crédit. Maximum sur 7 j = 300 + 600 + 900 = 1800 s = 30 min. Jouer
-- une difficulté déjà récompensée reste possible : la partie se termine
-- normalement, ne crédite rien (outcome 'cooldown_active') et ne consomme pas
-- d'éligibilité.
--
--   memory_games
--     UNE partie. `game_id` UUID imprévisible (uuid4) = PRIMARY KEY.
--     `status` ∈ ('active','completed','expired'). La finalisation
--     EXACTLY-ONCE est garantie EN BASE :
--       UPDATE memory_games SET status='completed' ...
--        WHERE game_id = %s AND user_id = %s AND status = 'active'
--     + garde `rowcount = 1`. Deux POST /complete concurrents / un rejeu / une
--     retransmission réseau / un changement d'appareil ne peuvent pas fermer
--     deux fois la même partie. `elapsed_seconds`, `reward_seconds`,
--     `reward_credited`, `outcome` sont écrits PAR LE SERVEUR à la
--     finalisation. `outcome` ∈ 'rewarded' | 'time_limit_exceeded' |
--     'cooldown_active' | 'implausible_time' | 'expired'.
--
--     Durée minimale plausible (bloque start->complete immédiat / horloge
--     trafiquée) : easy 4 s · medium 6 s · hard 9 s (planchers très en-dessous
--     de tout temps humain réel ; les seuils de récompense étant 20/40/80 s).
--     Expiration `game_id` : easy/medium 10 min, hard 15 min — au-delà,
--     /complete renvoie status='expired' sans crédit.
--
--   memory_rewards
--     UN crédit de récompense accordé. `game_id` = PRIMARY KEY -> une partie ne
--     crédite qu'UNE fois, à vie :
--       INSERT INTO memory_rewards (...) VALUES (...)
--         ON CONFLICT (game_id) DO NOTHING
--     + garde `rowcount = 1` sous `accounts ... FOR UPDATE` (mutex par
--     utilisateur, MÊME verrou que le moteur temps / J5 / J7). L'éligibilité 7
--     j est une simple lecture
--       SELECT MAX(credited_at) FROM memory_rewards
--        WHERE user_id = %s AND difficulty = %s
--     Le crédit passe par
--       UPDATE accounts SET purchased_seconds_remaining = COALESCE(...,0) + N
--        WHERE user_id = %s
--     après l'INSERT dont `rowcount = 1`. Il atterrit dans
--     accounts.purchased_seconds_remaining (bucket « temps acheté / crédité »,
--     jamais remis à zéro par un reset mensuel, consommé en dernier —
--     migration v34) et apparaît immédiatement dans GET /api/consultation/state
--     (bloc `time`).
--
-- FK memory_games.user_id   -> accounts(user_id) SANS ON DELETE CASCADE
-- FK memory_rewards.user_id -> accounts(user_id) SANS ON DELETE CASCADE
-- (cohérent avec le reste du schéma app : DELETE /api/app/account purge
-- explicitement les 2 tables — cf. _ACCOUNT_DELETE_CHILD_TABLES). Blocs DO $$
-- idempotents (n'attrapent que duplicate_object) — même idiome que
-- fk_wellbeing_cycle_rewards_account (v38).
--
-- Idempotence : CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS sont
-- des no-op au rejeu de init_db() ; les FK sont protégées par les DO $$.

CREATE TABLE IF NOT EXISTS memory_games (
    game_id          UUID         PRIMARY KEY,
    user_id          UUID         NOT NULL,
    difficulty       TEXT         NOT NULL,
    started_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ,
    status           TEXT         NOT NULL DEFAULT 'active',
    elapsed_seconds  INTEGER,
    reward_seconds   INTEGER      NOT NULL DEFAULT 0,
    reward_credited  BOOLEAN      NOT NULL DEFAULT FALSE,
    outcome          TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memory_games_user_status
ON memory_games (user_id, status);

CREATE TABLE IF NOT EXISTS memory_rewards (
    game_id           UUID         PRIMARY KEY,
    user_id           UUID         NOT NULL,
    difficulty        TEXT         NOT NULL,
    credited_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    credited_seconds  INTEGER      NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_rewards_user_difficulty
ON memory_rewards (user_id, difficulty, credited_at DESC);

DO $$
BEGIN
    ALTER TABLE memory_games
        ADD CONSTRAINT fk_memory_games_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    ALTER TABLE memory_rewards
        ADD CONSTRAINT fk_memory_rewards_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
