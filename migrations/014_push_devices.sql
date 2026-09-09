-- 014_push_devices.sql — Migration v40 : PUSH ANDROID MULTI-APPAREIL (FCM).
--
-- Miroir LISIBLE du bloc « Migration v40 » exécuté réellement par init_db()
-- dans auryel_bot.py. PUREMENT ADDITIF : 2 tables neuves + 2 FK idempotentes.
-- Aucune colonne existante ALTER-ée, aucun backfill, aucun DROP / TRUNCATE /
-- DELETE, aucune donnée existante modifiée.
--
--   push_devices
--     UN appareil = UN jeton FCM. `fcm_token` UNIQUE : l'upsert
--     (POST /api/app/push/register) réaffecte proprement un jeton au compte
--     courant si le téléphone a changé de compte :
--       INSERT INTO push_devices (...) VALUES (...)
--         ON CONFLICT (fcm_token) DO UPDATE SET
--           user_id = EXCLUDED.user_id, enabled = TRUE,
--           updated_at = NOW(), last_seen_at = NOW(),
--           platform = EXCLUDED.platform, app_version = ..., os_version = ...,
--           revoked_at = NULL, invalid_at = NULL
--     `enabled`    = interrupteur logique : logout de CET appareil,
--                    unregister explicite d'un jeton. NE désactive jamais les
--                    autres appareils du compte.
--     `revoked_at` = suppression de compte (purge complète, cf.
--                    _ACCOUNT_DELETE_CHILD_TABLES).
--     `invalid_at` = jeton définitivement rejeté par FCM (UNREGISTERED /
--                    INVALID_ARGUMENT). enabled repasse FALSE, jamais réactivé.
--     Le jeton n'est JAMAIS écrit dans un log.
--
--   notification_sends
--     Journal d'idempotence de l'envoi programmé (Phase scheduler).
--     `dedupe_key` UNIQUE = f"{category}:{user_id}:{periode Europe/Paris}" :
--       - daily_thought / daily_meditation : periode = date locale (AAAA-MM-JJ)
--       - weekly_sleep / weekly_life_lesson : periode = AAAA-Www (ISO) + jour
--     Un INSERT ... ON CONFLICT (dedupe_key) DO NOTHING + garde rowcount = 1
--     garantit « au plus un envoi par catégorie / utilisateur / période »,
--     robuste au redémarrage Railway, au redéploiement, et à un cron qui
--     tourne souvent (le job décide lui-même si un envoi est dû).
--     `status` ∈ ('sent','skipped_no_device','skipped_disabled','dry_run',
--                 'failed').
--     `erreur` : message générique borné (<= 200 caractères), JAMAIS de jeton
--     FCM ni de donnée personnelle.
--     Instants persistés en UTC (TIMESTAMPTZ). Le calcul métier applique
--     ZoneInfo("Europe/Paris") au moment de la lecture.
--
-- FK push_devices.user_id      -> accounts(user_id) SANS ON DELETE CASCADE
-- FK notification_sends.user_id -> accounts(user_id) SANS ON DELETE CASCADE
-- (cohérent avec le reste du schéma app : DELETE /api/app/account purge
-- explicitement les 2 tables — cf. _ACCOUNT_DELETE_CHILD_TABLES). Blocs DO $$
-- idempotents (n'attrapent que duplicate_object).
--
-- Idempotence : CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS sont
-- des no-op au rejeu de init_db() ; les FK sont protégées par les DO $$.

CREATE TABLE IF NOT EXISTS push_devices (
    id            UUID         PRIMARY KEY,
    user_id       UUID         NOT NULL,
    fcm_token     TEXT         NOT NULL UNIQUE,
    platform      TEXT         NOT NULL DEFAULT 'android',
    app_version   TEXT,
    os_version    TEXT,
    device_label  TEXT,
    enabled       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_seen_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    revoked_at    TIMESTAMPTZ,
    invalid_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_push_devices_user_enabled
ON push_devices (user_id, enabled);

CREATE TABLE IF NOT EXISTS notification_sends (
    id                   UUID         PRIMARY KEY,
    user_id              UUID         NOT NULL,
    category             TEXT         NOT NULL,
    dedupe_key           TEXT         NOT NULL UNIQUE,
    status               TEXT         NOT NULL,
    provider_message_id  TEXT,
    erreur               TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    sent_at              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_notification_sends_user_cat
ON notification_sends (user_id, category, created_at DESC);

DO $$
BEGIN
    ALTER TABLE push_devices
        ADD CONSTRAINT fk_push_devices_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    ALTER TABLE notification_sends
        ADD CONSTRAINT fk_notification_sends_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
