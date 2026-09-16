"""Contrats Lot 3E : le prompt final ne doit pas réactiver l'ancienne doctrine."""

from pathlib import Path

import auryel_bot as A


USER = {"prenom": "Camille", "genre": "f", "guide": "selena"}


def test_anti_evasion_answers_without_predicting():
    prompt = A.get_system_prompt(USER, "selena")
    assert "RÈGLE ANTI-ESQUIVE — RÉPONDRE SANS INVENTER" in prompt
    assert "ne permettent pas de savoir si quelqu'un reviendra" in prompt
    assert "son comportement depuis son départ" in prompt
    assert "Est-ce qu'il a repris contact de lui-même ?" in prompt
    anti = prompt.split("RÈGLE ANTI-ESQUIVE — RÉPONDRE SANS INVENTER", 1)[1]
    anti = anti.split("INTERDIT absolu", 1)[0]
    for forbidden in (
        "confirmation nette qu'un retour reste possible",
        "négation d'une fin définitive",
        "pas tout de suite, mais rien n'est fermé",
        "confirmation, avec un blocage identifié",
        "confirmation claire, dans la voix du conseiller, qu'un retour reste possible",
    ):
        assert forbidden not in anti


def test_guidance_is_symbolic_but_not_private_knowledge():
    prompt = A.get_system_prompt(USER, "ezra")
    assert "La posture de voyance influence le ton" in prompt
    assert "symboles" in prompt
    assert "n'autorise jamais à présenter comme une connaissance certaine" in prompt
    assert "ce que je perçois de ta situation reste juste" not in prompt
    assert "Il revient quand tu t'éloignes" not in prompt
    assert "Le nombre ne ferme rien" not in prompt


def test_short_and_why_rules_reconstruct_the_previous_fact():
    source = Path("auryel_bot.py").read_text(encoding="utf-8")
    assert "reformule le fait confirmé puis demande" in source
    assert "ce qui a été dit" in source
    assert "avant toute nouvelle question" in source
    assert "révèle la vraie intention d'un tiers" in source
    assert "Ne transforme pas ce mot en symbole" in source


def test_relationship_discovery_remains_gradual_and_personalized():
    prompt = A.get_system_prompt(USER, "cassandre")
    assert "UNE question" in prompt
    assert "prénom" in prompt
    assert "date de naissance" in prompt
    assert "lecture symbolique" in prompt
    assert "ne demande jamais prénom" in prompt
    assert "ne redemande pas une information déjà connue" in prompt
    assert "FAIT rapporté, INTERPRÉTATION de l'utilisateur, INCONNU" in prompt


def test_ten_personalities_and_symbolic_paths_remain_available():
    prompts = [A.get_system_prompt(USER, key) for key in A.GUIDES]
    assert len(set(prompts)) == 10
    assert all("TIRAGE DE CARTES AVEC CONSENTEMENT" in prompt for prompt in prompts)
    assert all("signe" in prompt.lower() for prompt in prompts)


def test_trompery_and_uncertainty_rules_remain_strict():
    prompt = A.get_system_prompt(USER, "thea")
    assert "ne constitue pas une preuve de tromperie" in prompt
    assert "FAIT rapporté, INTERPRÉTATION de l'utilisateur, INCONNU" in prompt
    assert "Ne dis pas qu'il pense encore" in prompt
    assert "qu'il va revenir" in prompt
