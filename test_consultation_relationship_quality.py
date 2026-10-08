"""Contrats déterministes du Lot 3D pour la continuité relationnelle.

Ces tests vérifient le prompt construit et les règles de contexte, sans appel
LLM ni dépendance à une réponse générée non déterministe.
"""

from pathlib import Path

import auryel_bot as A


USER = {"prenom": "Alex", "genre": "f", "guide": "selena"}


def _flat(prompt):
    return " ".join(prompt.split())


def test_short_messages_use_context_without_psychological_invention():
    source = Path("auryel_bot.py").read_text(encoding="utf-8")
    assert '"ok", "oui", "non", "d\'accord"' in source
    assert "ce que ce mot signifie dans l'échange" in source
    assert "Ne transforme pas ce mot en symbole" in source
    assert "Explique directement la réponse ou le point qui précède" in source


def test_security_and_economy_contracts_are_untouched():
    prompt = A.get_system_prompt(USER, "kael")
    assert "3114" in prompt
    assert "Ne jamais faire de diagnostic" in prompt
    for legacy in ("7,99", "8 h", "4 consultations", "10 consultations"):
        assert legacy not in prompt


def test_short_message_normalization_covers_requested_variants():
    assert A._conversation_mode("Oui.") == "brief"
    assert A._conversation_mode("D'accord.") == "brief"
    assert A._conversation_mode("Non.") == "brief"
    assert A._conversation_mode("Pourquoi ?") == "explanation"

