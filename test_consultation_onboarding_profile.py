"""Contrats déterministes du profil onboarding dans Consultation Lot 3C."""

from pathlib import Path

import auryel_bot as A


USER = {
    "prenom": "Alex",
    "genre": "f",
    "guide": "selena",
    "chemin_de_vie": "7",
    "signe_zodiaque": "Taureau",
}


def test_onboarding_profile_is_presented_as_hypothesis_once():
    prompt = A.get_system_prompt(USER, "selena", onboarding_profile_intro=True)
    assert "PREMIÈRE PRISE DE CONTACT" in prompt
    assert "chemin de vie 7" in prompt
    assert "signe Taureau" in prompt
    assert "HYPOTHÈSE" in prompt
    assert "si cela correspond" in prompt
    assert "tu es" in prompt
    assert "ne doit pas être répétée" in prompt


def test_profile_confirmation_and_correction_are_explicit():
    assert A._onboarding_profile_feedback("Oui.") == "confirmed"
    assert A._onboarding_profile_feedback("Pas vraiment.") == "corrected"
    assert A._onboarding_profile_feedback("je me reconnais") == "confirmed"
    assert A._onboarding_profile_feedback("ça ne me correspond pas") == "corrected"
    assert A._onboarding_profile_feedback("Oui, c’est ça.") == "confirmed"
    assert A._onboarding_profile_feedback("je ne sais pas") is None

    prompt = A.get_system_prompt(USER, "luna", onboarding_profile_feedback=True)
    assert "PROFIL À PRÉCISER" in prompt
    assert "Ne défends pas ton interprétation" in prompt


def test_confirmed_profile_is_global_context_but_not_narrative_memory():
    user = {**USER, "profile_self_description": "Je me décris comme réservé et attentif."}
    prompt = A.get_system_prompt(user, "orion")
    assert "PROFIL PERSONNEL CONFIRMÉ" in prompt
    assert "réservé et attentif" in prompt
    assert "user_advisor_memory" not in prompt


def test_app_profile_adapter_keeps_global_profile_fields():
    adapted = A._app_profile_to_user_dict({
        "user_id": "u",
        "onboarding_done": True,
        "chemin_de_vie": "7",
        "signe_zodiaque": "Taureau",
        "onboarding_profile_status": "confirmed",
        "profile_self_description": "Je suis réservé.",
    })
    assert adapted["onboarding_done"] is True
    assert adapted["onboarding_profile_status"] == "confirmed"
    assert adapted["profile_self_description"] == "Je suis réservé."


def test_memory_isolation_is_by_user_and_advisor():
    source = Path("auryel_bot.py").read_text(encoding="utf-8")
    assert "WHERE user_id=%s AND advisor_id=%s" in source
    assert "JOIN consultations c ON c.id = m.consultation_id" in source
    assert "never a memory narrative" not in source.lower()


def test_short_messages_do_not_require_hidden_emotion():
    source = Path("auryel_bot.py").read_text(encoding="utf-8")
    assert "ne révèle pas automatiquement une émotion" in source
    assert "Explique directement la réponse" in source
    assert "RÈGLE DE RELANCE — EXCEPTION UTILE" in A.get_system_prompt(
        USER, "selena", conversation_mode="brief")


def test_safety_and_economy_remain_untouched_in_prompt_contract():
    prompt = A.get_system_prompt(USER, "kael")
    assert "3114" in prompt
    assert "Ne jamais faire de diagnostic" in prompt
    for legacy in ("7,99", "8 h", "4 consultations", "10 consultations"):
        assert legacy not in prompt


def test_migration_is_additive_and_wired_after_038():
    migration = Path("migrations/039_consultation_onboarding_profile.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS onboarding_profile_status" in migration
    assert "ADD COLUMN IF NOT EXISTS profile_self_description" in migration
    assert "DROP" not in migration.upper()
    assert "TRUNCATE" not in migration.upper()
    source = Path("auryel_bot.py").read_text(encoding="utf-8")
    assert '"038_consultation_message_idempotency.sql"' in source
    assert '"039_consultation_onboarding_profile.sql"' in source
    assert "claim_onboarding_profile_intro(user_id)" in source
    assert "onboarding_profile_intro=onboarding_profile_intro" in source
