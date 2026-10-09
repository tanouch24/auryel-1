-- Roue cadeau (09/10/2026) : +3 minutes offertes UNE fois par compte, quand le
-- temps gratuit est presque épuisé. Marqueur à usage unique sur `accounts`.
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS gift_wheel_credited_at TIMESTAMPTZ;
