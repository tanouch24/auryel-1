-- 018_mobile_purchases.sql — Migration v44 : ACHATS CONSOMMABLES MOBILES
-- (« +1 heure supplémentaire » — produit Google Play `auryel_extra_hour`).
--
-- Miroir LISIBLE du bloc « Migration v44 » exécuté réellement par init_db()
-- dans auryel_bot.py. PUREMENT ADDITIF : 1 table neuve + 1 index + 1 FK
-- idempotente. Aucune colonne existante ALTER-ée, aucun backfill, aucun
-- DROP / TRUNCATE / DELETE. La souscription Premium (mobile_subscriptions,
-- consultation_allowance, resync_premium_entitlement) n'est PAS touchée.
--
--   mobile_purchases
--     UN achat consommable DÉJÀ vérifié auprès du store officiel ET crédité.
--       store        'google_play' (| 'app_store', lot ultérieur)
--       product_id   'auryel_extra_hour'
--       purchase_key purchaseToken Google (| transactionId Apple). Chaque
--                    achat DISTINCT du même produit répétable produit un
--                    NOUVEAU token -> une NOUVELLE ligne -> +credited_seconds.
--       order_id     orderId store (traçabilité / support), facultatif
--       credited_seconds  mapping SERVEUR FIGÉ : auryel_extra_hour => 3600.
--                    JAMAIS une valeur fournie par le client.
--       status       'credited'
--       raw_payload  sous-ensemble NON sensible de la réponse store
--                    (purchase_state, consumption/ack state, region...).
--                    JAMAIS le purchaseToken en clair au-delà de purchase_key.
--
--     ANTI-DOUBLE-CRÉDIT (garanti EN BASE) :
--       CONSTRAINT uq_mobile_purchases_store_key UNIQUE (store, purchase_key)
--     + INSERT ... ON CONFLICT (store, purchase_key) DO NOTHING sous
--       `accounts ... FOR UPDATE` + garde `rowcount == 1` avant tout crédit.
--     Rejeu du même token / requêtes concurrentes / réinstallation /
--     changement d'appareil : au plus 1 ligne, au plus 1 crédit.
--
--     Le crédit atterrit dans accounts.purchased_seconds_remaining (bucket
--     'purchased' : débité en DERNIER — first_free -> premium -> earned ->
--     purchased — jamais remis à zéro par un reset mensuel) et est tracé au
--     time_ledger (reason = 'purchase_extra_hour', ref_id = mobile_purchases.id)
--     dans la MÊME transaction. earned_seconds_remaining et le quota Premium
--     ne sont JAMAIS touchés.
--
-- FK ... -> accounts(user_id) SANS ON DELETE CASCADE. DELETE /api/app/account
-- NE purge PAS mobile_purchases : une preuve d'achat créditée impose
-- l'ANONYMISATION du compte (comme mobile_subscriptions), jamais la
-- suppression pure — cf. api_app_account_delete. Bloc DO $$ idempotent
-- (duplicate_object seulement) — idiome v27/v37/v38.
--
-- Idempotence init_db() : CREATE TABLE / INDEX IF NOT EXISTS = no-op au rejeu ;
-- le DO $$ n'attrape que duplicate_object.

CREATE TABLE IF NOT EXISTS mobile_purchases (
    id               UUID         PRIMARY KEY,
    user_id          UUID         NOT NULL,
    store            TEXT         NOT NULL,
    product_id       TEXT         NOT NULL,
    purchase_key     TEXT         NOT NULL,
    order_id         TEXT,
    credited_seconds INTEGER      NOT NULL,
    status           TEXT         NOT NULL DEFAULT 'credited',
    purchased_at     TIMESTAMPTZ,
    credited_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    raw_payload      JSONB,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_mobile_purchases_store_key UNIQUE (store, purchase_key)
);

CREATE INDEX IF NOT EXISTS idx_mobile_purchases_user
ON mobile_purchases (user_id, credited_at DESC);

DO $$
BEGIN
    ALTER TABLE mobile_purchases
        ADD CONSTRAINT fk_mobile_purchases_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
