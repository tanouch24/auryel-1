-- Économie Étoiles v3 : barème final.
-- Idempotent, additif : wallets, ledgers et temps existants sont conservés.
UPDATE reward_rules SET stars_amount = 6, enabled = TRUE, daily_limit = 5,
    updated_at = NOW()
WHERE rule_key = 'rewarded_ad_completed';

UPDATE reward_rules SET stars_amount = 1, enabled = TRUE, daily_limit = 1,
    updated_at = NOW()
WHERE rule_key IN (
    'meditation_completed', 'mini_game_completed', 'daily_card_completed',
    'tarot_completed', 'wake_completed', 'share_completed'
);

UPDATE reward_rules SET stars_amount = 5, enabled = TRUE, daily_limit = NULL,
    updated_at = NOW()
WHERE rule_key = 'streak_7_days';

-- Le plafond mensuel est calculé depuis express_consultations, le ledger
-- append-only des conversions réussies. Aucun état de wallet n'est ajouté.
