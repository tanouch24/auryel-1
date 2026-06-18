-- migrations/004_derniere_verif_stripe_at.sql
-- Colonne anti-zombie : timestamp de la dernière vérification Stripe pour les abonnés actifs.
-- Permet de vérifier 1×/semaine par user que l'abonnement est toujours actif côté Stripe.
-- Déjà appliqué en production via init_db() migration v9.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS derniere_verif_stripe_at TEXT DEFAULT '';
