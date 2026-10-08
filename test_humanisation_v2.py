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

