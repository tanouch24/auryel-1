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

# L'endpoint n'accepte jamais un montant fourni par le client et exige une
# clé d'événement distincte pour une rewarded réellement terminée.
assert '"rewarded_ad_completed"' in source
assert 'data.get("event_id")' in source
assert '"invalid_event_id"' in source
print("OK stars economy v4: barème, désactivation et idempotence rewarded")
