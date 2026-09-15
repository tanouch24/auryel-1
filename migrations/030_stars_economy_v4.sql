-- Économie Étoiles v4 — additif et idempotent.
-- Les wallets, transactions et historiques existants sont conservés.
UPDATE reward_rules
SET stars_amount = 6, enabled = TRUE, daily_limit = NULL,
    updated_at = NOW()
WHERE rule_key = 'rewarded_ad_completed';

UPDATE reward_rules
SET stars_amount = 2, enabled = TRUE, daily_limit = 1,
    updated_at = NOW()
WHERE rule_key IN (
    'mini_game_completed', 'tarot_completed', 'wake_completed',
    'share_completed'
);

UPDATE reward_rules
SET enabled = FALSE, updated_at = NOW()
WHERE rule_key IN (
    'daily_card_completed', 'meditation_completed', 'streak_7_days'
);
