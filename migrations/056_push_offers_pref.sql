-- Interrupteur « Offres » (10/10/2026) : la personne peut couper les
-- notifications d'offres (premium_offer, gift_question). Activé par défaut,
-- annoncé dans les réglages et la confidentialité.
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS push_offers_enabled BOOLEAN NOT NULL DEFAULT TRUE;
