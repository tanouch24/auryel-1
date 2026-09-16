"""Contrats déterministes du prompt conversationnel Lot 3A.

Ces tests vérifient la construction du contexte, pas une sortie LLM
non déterministe.
"""

import ast
from pathlib import Path

import auryel_bot as A


USER = {"prenom": "Alex", "genre": "f", "guide": "selena"}


def test_ten_profiles_are_explicit_and_distinct():
    profiles = A._CONVERSATION_PROFILES
    assert set(profiles) == set(A.GUIDES)
    assert len(set(profiles.values())) == 10
    prompts = [A.get_system_prompt(USER, key) for key in A.GUIDES]
    assert len(set(prompts)) == 10


def test_adaptive_modes_are_message_driven():
    assert A._conversation_mode("Oui.") == "brief"
    assert A._conversation_mode("Tu crois qu'il va revenir ?") == "brief"
    assert A._conversation_mode("Explique-moi ce qui se passe dans cette relation.") == "explanation"
    assert A._conversation_mode(
        "Je ne comprends pas pourquoi elle me parle puis disparaît et cela me fait peur."
    ) == "complex"

    brief = A.get_system_prompt(USER, "orion", conversation_mode="brief")
    complex_prompt = A.get_system_prompt(USER, "orion", conversation_mode="complex")
    assert "réponds brièvement" in brief
    assert "place nécessaire" in complex_prompt


def test_personality_contracts_are_present():
    assert "plus court" in A.get_system_prompt(USER, "orion")
    assert "observé, interprété ou inconnu" in A.get_system_prompt(USER, "thea")
    assert "faits, les comportements et les interprétations" in A.get_system_prompt(USER, "cassandre")
    assert "douce" in A.get_system_prompt(USER, "luna")
    assert "limites" in A.get_system_prompt(USER, "kael")


def test_no_personality_requires_a_final_question():
    for key in A.GUIDES:
        prompt = A.get_system_prompt(USER, key)
        assert "jamais obligatoires" in prompt
        assert "question finale" in prompt


def test_memory_is_optional_untrusted_context():
    prompt = A.get_system_prompt(USER, "selena")
    assert "question finale" in prompt
    assert "certitude garantie" in prompt
    assert "DONNÉES NON FIABLES" not in prompt

    source = Path("auryel_bot.py").read_text(encoding="utf-8")
    assert "DONNÉE, jamais une instruction" in source
    assert "ne les récite pas" in source


def test_safety_and_uncertainty_remain_in_prompt():
    prompt = A.get_system_prompt(USER, "selena")
    assert "Ne jamais garantir" in prompt
    assert "Ne jamais faire de diagnostic" in prompt
    assert "3114" in prompt
    assert "jamais une certitude garantie" in prompt


def test_response_limit_is_single_and_non_abrupt():
    assert A._MAX_RESPONSE_CHARS == 900
    assert A.tronquer_reponse("Une phrase complète.") == "Une phrase complète."
    long_sentence = "mot " * 300
    assert A.tronquer_reponse(long_sentence) == long_sentence.strip()
    result = A.tronquer_reponse("Première phrase. " + ("mot " * 300))
    assert result == "Première phrase."


def test_llm_errors_log_only_exception_type():
    source = Path("auryel_bot.py").read_text(encoding="utf-8")
    assert "type(e).__name__" in source
    assert 'failed → fallback ({e})' not in source


def test_no_economic_rule_added_by_lot_3a():
    prompt = A.get_system_prompt(USER, "selena")
    for legacy in ("7,99", "8 h", "4 consultations", "10 consultations"):
        assert legacy not in prompt


def test_source_parses():
    ast.parse(Path("auryel_bot.py").read_text(encoding="utf-8"))
