"""Contrats Lot 3E : le prompt final ne doit pas réactiver l'ancienne doctrine."""

from pathlib import Path

import auryel_bot as A


USER = {"prenom": "Camille", "genre": "f", "guide": "selena"}


def test_short_and_why_rules_reconstruct_the_previous_fact():
    source = Path("auryel_bot.py").read_text(encoding="utf-8")
    assert "reformule le fait confirmé puis demande" in source
    assert "ce qui a été dit" in source
    assert "avant toute nouvelle question" in source
    assert "révèle la vraie intention d'un tiers" in source
    assert "Ne transforme pas ce mot en symbole" in source

