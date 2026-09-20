"""Contrats déterministes de l'humanisation V2.

Ces tests vérifient les règles injectées dans le moteur commun, pas une sortie
LLM non déterministe. Ils garantissent aussi que les dix voix restent
différenciées sans créer dix moteurs de conversation.
"""

import os
import sys
from pathlib import Path

from unittest.mock import MagicMock

sys.modules.setdefault("psycopg2", MagicMock())
for _key, _value in {
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "SECRET_KEY": "test", "ADMIN_PASSWORD": "test",
}.items():
    os.environ.setdefault(_key, _value)

import auryel_bot as A


USER = {"prenom": "Alex", "genre": "f", "guide": "selena"}


def test_common_human_contract_is_present_for_every_advisor():
    prompts = {key: A.get_system_prompt(USER, key) for key in A.GUIDES}
    assert len(prompts) == 10
    for prompt in prompts.values():
        assert "CONVERSATION NATURELLE — RÈGLES COMMUNES" in prompt
        assert "MOTIVATION SITUÉE" in prompt
        assert "CONTINUITÉ FIABLE" in prompt
        assert "Aucune question" not in prompt
        assert "Une question est facultative" in prompt
        assert "N'imite pas les hésitations" in prompt


def test_ten_behavior_profiles_are_distinct_and_injected():
    assert set(A._HUMAN_BEHAVIOR_PROFILES) == set(A.GUIDES)
    assert len(set(A._HUMAN_BEHAVIOR_PROFILES.values())) == 10
    for key, profile in A._HUMAN_BEHAVIOR_PROFILES.items():
        prompt = A.get_system_prompt(USER, key)
        assert profile in prompt
        assert "directivité" in profile
        assert "motiv" in profile
        assert "question" in profile


def test_humanisation_scenarios_select_proportionate_modes():
    scenarios = {
        "discouraged": ("Je n'en peux plus, je suis découragé.", "complex"),
        "real_success": ("J'ai enfin envoyé le dossier aujourd'hui, après plusieurs semaines.", "standard"),
        "procrastination": ("Je repousse encore cette tâche depuis plusieurs jours et cela me bloque vraiment.", "standard"),
        "anxious_before_action": ("J'ai peur avant de lui parler demain.", "complex"),
        "simple_information": ("Pourquoi ça arrive ?", "explanation"),
        "thank_you": ("Merci.", "brief"),
        "wants_to_talk": ("J'ai besoin de parler un peu ce soir, sans forcément chercher une solution tout de suite.", "standard"),
        "direct_advice": ("Je lui écris ou pas ?", "brief"),
        "repeated_worry": ("Je pense encore à la même chose depuis hier soir.", "standard"),
        "natural_closure": ("D'accord, je vais essayer.", "brief"),
    }
    for message, expected_mode in scenarios.values():
        assert A._conversation_mode(message) == expected_mode


def test_motivation_is_contextual_not_automatic():
    prompt = A.get_system_prompt(USER, "maia", conversation_mode="standard")
    assert "Encourage seulement à partir d'un fait du tour" in prompt
    assert "une petite étape concrète peut aider" in prompt
    assert "Une demande simple d'information, un merci ou une clôture" in " ".join(prompt.split())
    assert "Ne félicite jamais une action inconnue" in prompt
    assert "ne fabrique ni progrès, ni objectif, ni souvenir" in " ".join(prompt.split())


def test_memory_and_recommendation_boundaries_remain_real_only():
    prompt = A.get_system_prompt(USER, "thea")
    assert "si tu n'es pas certain, ne dis pas que tu t'en souviens" in prompt
    assert "Ne force ni recommandation de contenu" in prompt
    assert "DONNÉES NON FIABLES" not in prompt
    source = Path("auryel_bot.py").read_text(encoding="utf-8")
    assert "CONTINUITÉ UTILE (DONNÉES NON FIABLES)" in source
    assert "WHERE user_id=%s AND advisor_id=%s" in source


def test_direct_answer_and_no_automatic_question_rules_are_explicit():
    prompt = A.get_system_prompt(USER, "selena", conversation_mode="brief")
    assert "Réponds d'abord à ce que la personne vient réellement de dire" in prompt
    assert "ne termine pas automatiquement par une question" in prompt
    assert "Une question finale, une image, un conseil ou une proposition supplémentaire sont facultatifs" in " ".join(prompt.split())
    assert "La longueur, le rythme et la question finale ne sont jamais obligatoires" in prompt
    assert "Pour un message simple, un merci, une affirmation ou un conseil déjà complet" in prompt
    assert "ils ne constituent jamais une consigne de terminer chaque réponse par une question" in " ".join(prompt.split())


def test_adaptive_length_contract_covers_short_and_complex_messages():
    prompt = " ".join(A.get_system_prompt(USER, "selena").split())
    assert "Un message très court ou une demande simple appelle en général une réponse brève et directe" in prompt
    assert "souvent en une ou deux phrases si cela suffit" in prompt
    assert "Une situation complexe, émotionnelle ou ambiguë peut demander davantage de développement" in prompt
    assert "ne tronque jamais une réponse utile" in prompt


def test_adaptive_length_modes_remain_deterministic():
    assert A._conversation_mode("Merci.") == "brief"
    assert A._conversation_mode("Tu penses que je devrais lui écrire ?") == "brief"
    assert A._conversation_mode("Je commence par quoi ?") == "brief"
    assert A._conversation_mode(
        "Je suis épuisé par cette situation, j'ai peur de devoir choisir entre "
        "plusieurs options et je ne sais plus comment avancer sans blesser quelqu'un."
    ) == "complex"
    assert A._conversation_mode(
        "J'ai peur avant demain et cette peur revient depuis plusieurs jours, "
        "avec beaucoup de pensées contradictoires."
    ) == "complex"


def test_humanisation_does_not_create_an_excessive_prompt():
    prompt = A.get_system_prompt(USER, "selena")
    # Contrat commun compact : la voix reste sous un plafond raisonnable malgré
    # les garde-fous historiques et les exemples persona.
    assert len(A.BLOC_HUMANISATION_COMMUNE) < 2400
    assert len(prompt) < 25000


def test_safety_and_third_party_rules_survive_humanisation():
    prompt = A.get_system_prompt(USER, "cassandre")
    assert "3114" in prompt
    assert "Ne jamais faire de diagnostic" in prompt
    assert "ne prouve pas sa pensée, son sentiment ou son intention privée" in " ".join(prompt.split())
    assert "FAIT rapporté, INTERPRÉTATION de l'utilisateur, INCONNU" in prompt
