-- Rewarded AdMob V5 : le reward serveur passe de 6 à 12 étoiles.
-- Additif et idempotent : aucun historique ni wallet existant n'est modifié.
UPDATE reward_rules
SET stars_amount = 12, enabled = TRUE, daily_limit = NULL, updated_at = NOW()
WHERE rule_key = 'rewarded_ad_completed';
