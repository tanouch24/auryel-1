"""Contrat statique de la migration Étoiles V4."""
from pathlib import Path

source = Path("auryel_bot.py").read_text()
migration = Path("migrations/030_stars_economy_v4.sql").read_text()

assert '"migrations",\n            "030_stars_economy_v4.sql"' in source
assert "Migration v57" in source
assert "rewarded_ad_completed" in migration
assert "stars_amount = 6" in migration
assert "daily_limit = NULL" in migration
for key in ("mini_game_completed", "tarot_completed", "wake_completed", "share_completed"):
    assert key in migration
assert "stars_amount = 2" in migration
for key in ("daily_card_completed", "meditation_completed", "streak_7_days"):
    assert key in migration
assert "enabled = FALSE" in migration

# Depuis SSV, le claim client historique refuse toute rewarded : le callback
# signé Google est l'unique chemin de crédit.
assert '"rewarded_ad_completed"' in source
assert '"/api/app/rewards/admob/ssv"' in source
assert '"admob_ssv_required"' in source
assert 'admob_reward_events' in source
print("OK stars economy v4: barème préservé, rewarded réservée à SSV")
