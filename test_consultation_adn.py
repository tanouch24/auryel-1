"""Contrats Lot 3F : préserver l'ADN guidance/confidence/coaching Auryel."""

import auryel_bot as A


USER = {"prenom": "Camille", "genre": "f", "guide": "selena"}


def test_personalized_coaching_and_motivation_are_allowed():
    prompt = A.get_system_prompt(USER, "maia")
    assert "COACHING ET MOTIVATION — PERSONNALISÉS" in prompt
    assert "redonner confiance" in prompt
    assert "proposer une action concrète" in prompt
    assert "conseil relationnel raisonnable" in prompt
    assert "l'aider à réfléchir à une décision" in prompt
    assert "découle réellement de ce qu'elle vient de raconter" in prompt


def test_generic_chatbot_coaching_is_rejected_without_banning_advice():
    prompt = A.get_system_prompt(USER, "cassandre")
    assert "coaching générique, automatique, impersonnel ou scolaire" in prompt
    assert "Ne transforme pas chaque tour en plan d'action" in prompt
    assert "Un conseil peut être direct" in prompt
    assert "ne constituent pas un format obligatoire à réciter" in prompt


def test_guidance_confidant_and_symbolic_registers_remain_present():
    prompt = A.get_system_prompt(USER, "ezra")
    assert "voyante / médium" in prompt
    assert "présence de guidance" in prompt
    assert "Registre de confident" in prompt or "registre de confident" in prompt
    assert "lecture symbolique" in prompt
    assert "tarot" in prompt.lower()
    assert "astrologie" in prompt.lower()
    assert "ils ne transforment jamais" in prompt


def test_lot3e_uncertainty_stays_strict_with_coaching_enabled():
    prompt = A.get_system_prompt(USER, "selena")
    for forbidden in (
        "Thomas pense encore à toi",
        "Thomas va revenir",
        "Il reviendra plus tard",
        "Rien n'est fermé",
        "Quelque chose le retient",
    ):
        assert forbidden not in prompt
    assert "ne permettent pas de savoir si quelqu'un reviendra" in prompt
    assert "ne constitue pas une preuve de tromperie" in prompt


def test_all_ten_profiles_keep_their_coaching_voice():
    prompts = {key: A.get_system_prompt(USER, key) for key in A.GUIDES}
    assert len(set(prompts.values())) == 10
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
    for key, phrase in expected.items():
        assert phrase in prompts[key]

