-- schema.sql — État complet de la base Auryel au 2026-06-17
-- Généré depuis l'audit 7.1 (\d users, \d messages sur Railway production)
-- Ce fichier permet de recréer la DB complète depuis zéro.
-- Les migrations individuelles sont dans migrations/001_*.sql et suivants.

-- ============================================================
-- TABLE users
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    phone                                   TEXT        PRIMARY KEY,
    email                                   TEXT        DEFAULT '',
    prenom                                  TEXT        DEFAULT '',
    guide                                   TEXT        DEFAULT 'séraphine',
    nom_affiche                             TEXT        DEFAULT '',
    nb_echanges                             INTEGER     DEFAULT 0,
    dernier_outil                           TEXT        DEFAULT '',
    date_premier_contact                    TEXT,
    date_dernier_contact                    TEXT,
    etat                                    TEXT        DEFAULT 'normal',
    abonne                                  BOOLEAN     DEFAULT FALSE,
    date_abonnement                         TEXT        DEFAULT '',
    stripe_customer_id                      TEXT        DEFAULT '',
    relance_j6_envoyee                      BOOLEAN     DEFAULT FALSE,
    relance_j8_envoyee                      BOOLEAN     DEFAULT FALSE,
    dernier_relance_abonne_at               TEXT        DEFAULT '',
    relance_abonne_count                    INTEGER     DEFAULT 0,
    dernier_rituel_date                     TEXT        DEFAULT '',
    dernier_rituel_type                     TEXT        DEFAULT '',
    depuis_site                             BOOLEAN     DEFAULT FALSE,
    prenoms_importants                      TEXT        DEFAULT '',
    theme_dominant                          TEXT        DEFAULT '',
    douleur_principale                      TEXT        DEFAULT '',
    peur_dominante                          TEXT        DEFAULT '',
    niveau_detresse                         INTEGER     DEFAULT 0,
    niveau_attachement                      INTEGER     DEFAULT 0,
    dernier_sujet_sensible                  TEXT        DEFAULT '',
    derniere_intention                      TEXT        DEFAULT '',
    relance_j7_envoyee                      BOOLEAN     DEFAULT FALSE,
    onboarding_step                         TEXT        DEFAULT 'prenom',
    onboarding_done                         BOOLEAN     DEFAULT FALSE,
    profil_initial                          TEXT,
    onboarding_question                     TEXT,
    onboarding_psaume                       INTEGER,
    derniere_relance_conversationnelle_at   TEXT        DEFAULT '',
    derniere_relance_conversationnelle_type TEXT        DEFAULT '',
    relance_hebdo_envoyee                   BOOLEAN     DEFAULT FALSE,
    derniere_relance_hebdo_at               TEXT        DEFAULT '',
    stop_relances                           BOOLEAN     DEFAULT FALSE
);

-- ============================================================
-- TABLE messages
-- ============================================================
CREATE TABLE IF NOT EXISTS messages (
    id        SERIAL  PRIMARY KEY,
    phone     TEXT,
    role      TEXT,
    content   TEXT,
    timestamp TEXT
);

-- ============================================================
-- INDEX
-- ============================================================
-- Partiel : exclut '' partagé par tous les non-abonnés
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_stripe_customer
    ON users (stripe_customer_id)
    WHERE stripe_customer_id != '';
