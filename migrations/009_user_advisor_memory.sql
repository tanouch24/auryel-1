-- 009_user_advisor_memory.sql — Migration v35 (B7) : mémoire inter-session
-- par conseiller.
--
-- Miroir LISIBLE du bloc « Migration v35 » exécuté réellement par init_db()
-- dans auryel_bot.py. PUREMENT ADDITIF : une table neuve + une FK idempotente.
-- Aucune colonne touchée ailleurs, aucun backfill, aucun DROP / TRUNCATE /
-- DELETE, aucune donnée existante modifiée.
--
-- summary : résumé NARRATIF compact (~150-200 mots). Un cap technique dur de
--   1500 caractères est appliqué CÔTÉ CODE (_MEMORY_SUMMARY_CHAR_CAP) avant
--   toute écriture — pas de contrainte de longueur en base. NOT NULL DEFAULT ''
--   -> une ligne fraîche = pas encore de mémoire, rien n'est injecté au prompt.
-- last_source_message_id : curseur. Dernier messages.id DÉJÀ résumé pour ce
--   couple (user_id, advisor_id). DEFAULT 0 -> la première reprise pertinente
--   résume une seule fois l'historique utile de CE conseiller ; les reprises
--   suivantes ne relisent QUE messages.id > last_source_message_id.
-- UNIQUE (user_id, advisor_id) : une seule mémoire par couple. L'index créé par
--   la contrainte sert aussi au SELECT de chargement.
--
-- facts_json : VOLONTAIREMENT ABSENT en V1. Les données globales structurées
-- (thème dominant, douleur, peur, prénoms importants, sujet sensible, intention,
-- attachement, détresse) vivent déjà dans app_profiles et ne sont PAS
-- dupliquées ici.
--
-- FK user_id -> accounts(user_id) avec ON DELETE CASCADE (B7.1). Divergence
-- ASSUMÉE vs le reste du schéma app (v22..v33 = SANS cascade, modèle soft-delete
-- via accounts.deleted_at) : la mémoire conseiller est une donnée PERSONNELLE
-- dérivée du compte, sans intérêt après suppression du compte. CASCADE évite que
-- cette FK bloque un futur hard-DELETE de compte (Dashboard / RGPD, lot B10) et
-- garantit zéro ligne mémoire orpheline. Table feuille : le CASCADE ne se
-- propage nulle part.
--
-- Idempotence : CREATE TABLE IF NOT EXISTS (contrainte UNIQUE inline) est un
-- no-op au rejeu ; la FK est posée dans un DO $$ ... EXCEPTION WHEN
-- duplicate_object (style identique à fk_tirages_account, v33).

CREATE TABLE IF NOT EXISTS user_advisor_memory (
    id                     UUID         PRIMARY KEY,
    user_id                UUID         NOT NULL,
    advisor_id             TEXT         NOT NULL,
    summary                TEXT         NOT NULL DEFAULT '',
    last_source_message_id BIGINT       NOT NULL DEFAULT 0,
    created_at             TIMESTAMPTZ  NOT NULL,
    updated_at             TIMESTAMPTZ  NOT NULL,
    UNIQUE (user_id, advisor_id)
);

-- FK idempotente vers accounts(user_id), AVEC ON DELETE CASCADE (B7.1 — cf.
-- en-tête : la mémoire conseiller disparaît automatiquement à la suppression du
-- compte).
DO $$
BEGIN
    ALTER TABLE user_advisor_memory
        ADD CONSTRAINT fk_user_advisor_memory_account
        FOREIGN KEY (user_id) REFERENCES accounts(user_id)
        ON DELETE CASCADE;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
