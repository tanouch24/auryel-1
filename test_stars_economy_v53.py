"""Contrat produit v3 : barème serveur et plafond de conversion mensuel."""
from pathlib import Path
import ast

source = Path("auryel_bot.py").read_text()
migration = Path("migrations/026_stars_economy_v3.sql").read_text()
tree = ast.parse(source)
constants = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        name = getattr(node.targets[0], "id", None)
        if name in {
            "STARS_MONTHLY_CONVERSION_LIMIT_MINUTES",
            "STARS_MONTHLY_CONVERSION_LIMIT_SECONDS",
        }:
            try:
                constants[name] = ast.literal_eval(node.value)
            except ValueError:
                pass

assert constants["STARS_MONTHLY_CONVERSION_LIMIT_MINUTES"] == 30
assert "STARS_MONTHLY_CONVERSION_LIMIT_SECONDS = (" in source
assert "STARS_MONTHLY_CONVERSION_LIMIT_MINUTES * 60" in source
expected = {
    "rewarded_ad_completed": (6, 5),
    "meditation_completed": (1, 1),
    "mini_game_completed": (1, 1),
    "daily_card_completed": (1, 1),
    "tarot_completed": (1, 1),
    "wake_completed": (1, 1),
    "share_completed": (1, 1),
    "streak_7_days": (5, None),
}
for key, (amount, limit) in expected.items():
    assert f"{amount}, {limit}, now53, \"{key}\"" in source
    assert key in migration

assert "COALESCE(SUM(seconds_granted), 0)" in source
assert "status='completed'" in source
assert "monthly_conversion_limit_reached" in source
assert "express_consultations" in source
print("OK stars economy v53: barème, ledger mensuel et plafond 30 min")
