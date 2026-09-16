"""Contrats déterministes du Lot 3D pour la continuité relationnelle.

Ces tests vérifient le prompt construit et les règles de contexte, sans appel
LLM ni dépendance à une réponse générée non déterministe.
"""

from pathlib import Path

import auryel_bot as A


USER = {"prenom": "Alex", "genre": "f", "guide": "selena"}


def test_relationship_questions_are_contextual_and_progressive():
    prompt = A.get_system_prompt(USER, "selena")
    assert "UNE question" in prompt
    assert "historique et la mémoire narrative autorisée" in prompt
    assert "ne redemande pas une information déjà connue" in prompt
    assert "ne transforme pas la consultation en questionnaire" in prompt
    assert "prénom" in prompt
    assert "chronologie" in prompt
    assert "comportement observable" in prompt
    assert "jamais une preuve" in prompt


def test_all_advisors_have_distinct_question_styles():
    expected = {
        "selena": "vécu concret",
        "ezra": "symbole précis",
        "cassandre": "fait vérifiable",
        "raphael": "blessure",
        "orion": "question rare et ciblée",
        "myriam": "choisir ou clarifier",
        "maia": "choix ou une action",
        "luna": "prendre soin",
        "thea": "séparent ces trois niveaux",
        "kael": "actes, les limites et la dignité",
    }
    for advisor, phrase in expected.items():
        assert phrase in A.get_system_prompt(USER, advisor)
    assert len({A._CONVERSATION_PROFILES[key] for key in A.GUIDES}) == 10


def test_short_messages_use_context_without_psychological_invention():
    source = Path("auryel_bot.py").read_text(encoding="utf-8")
    assert '"ok", "oui", "non", "d\'accord"' in source
    assert "ce que ce mot signifie dans l'échange" in source
    assert "Ne transforme pas ce mot en symbole" in source
    assert "Explique directement la réponse ou le point qui précède" in source


def test_third_party_uncertainty_and_cheating_are_fact_based():
    prompt = A.get_system_prompt(USER, "thea")
    assert "FAIT rapporté, INTERPRÉTATION de l'utilisateur, INCONNU" in prompt
    assert "Tu ne connais pas les pensées, sentiments, intentions ou décisions privées" in prompt
    assert "ne constitue pas une preuve de tromperie" in prompt
    assert "qu'il va revenir" in prompt
    assert "une question concrète sur un comportement observable" in prompt


def test_memory_and_profile_boundaries_remain_explicit():
    source = Path("auryel_bot.py").read_text(encoding="utf-8")
    assert "WHERE user_id=%s AND advisor_id=%s" in source
    assert "JOIN consultations c ON c.id = m.consultation_id" in source
    prompt = A.get_system_prompt(
        {**USER, "profile_self_description": "Je suis plutôt réservé."},
        "orion",
    )
    assert "PROFIL PERSONNEL CONFIRMÉ" in prompt
    assert "Une hypothèse reste une hypothèse" in prompt
    assert "ne les déplace pas dans le profil global" in prompt


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
    assert A._conversation_mode("Pourquoi ?") == "brief"
