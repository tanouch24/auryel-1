"""Contrat du prompt de consultation v3 (validé le 08/10/2026) : court,
humain, une seule règle d'incertitude, une fiche par guide alignée sur l'app.
Remplace les tests qui figeaient mot pour mot l'ancien texte."""
import os

os.environ.setdefault("SECRET_KEY", "t")
os.environ.setdefault("ADMIN_PASSWORD", "t")
os.environ.setdefault("DATABASE_URL", "postgresql://t:t@127.0.0.1:1/t")

import auryel_bot as A  # noqa: E402

USER = {"prenom": "Camille", "genre": "f"}
KEYS = ["selena", "luna", "maia", "thea", "cassandre",
        "myriam", "orion", "ezra", "kael", "raphael"]


def _p(key="selena", **kw):
    return A.get_system_prompt(USER, key, **kw)


def test_prompt_is_short_and_app_native():
    p = _p()
    assert len(p.split()) < 1400
    assert "application Auryel" in p
    assert "WhatsApp" not in p
    assert "Tu es Séléna" in p and "Camille" in p


def test_one_card_per_guide_aligned_and_distinct():
    assert set(A._FICHES_GUIDES) == set(KEYS)
    tons = set()
    for key in KEYS:
        p = _p(key)
        fiche = A._FICHES_GUIDES[key]
        assert f"Ton : {fiche['ton']}" in p
        assert all(e in p for e in fiche["exemples"])
        tons.add(fiche["ton"])
        # L'ancienne triple description n'est plus injectée.
        assert A._HUMAN_BEHAVIOR_PROFILES.get(key, "§") not in p
        assert A._CONVERSATION_PROFILES.get(key, "§") not in p
    assert len(tons) == len(KEYS)
    assert "directe et posée" in A._FICHES_GUIDES["selena"]["ton"]
    assert "tarot" in A._FICHES_GUIDES["myriam"]["domaine"]


def test_uncertainty_rule_said_once_without_mind_reading():
    p = _p("thea")
    assert "Tu ne lis pas dans les pensées des autres" in p
    assert "pas des preuves" in p
    assert "jamais une garantie pour la suite" in p
    assert "« tu me manques »" in p
    assert p.count("Tu ne lis pas dans les pensées") == 1
    assert "FAIT rapporté, INTERPRÉTATION" not in p


def test_takes_a_position_without_evasion():
    p = _p()
    assert "ta première phrase donne une vraie réponse" in p
    assert "c'est à toi de voir" in p  # cité comme interdit
    assert "Je pencherais pour partir" in p


def test_human_style_rules():
    p = _p()
    assert "Phrases courtes" in p
    assert "UNE question" in p
    assert "ne redemande pas une information déjà connue" in p
    assert "Tu ne t'inventes jamais de souvenirs" in p
    assert "jamais de slogan" in p
    assert "Je ressens" in p  # interdit en ouverture
    assert "jamais obligatoires" in p and "question finale" in p


def test_safety_rules_survive():
    for key in KEYS:
        p = _p(key)
        assert "3114" in p
        assert "Ne jamais garantir" in p
        assert "Ne jamais faire de diagnostic" in p
        assert "certitude absolue" in p
        assert "Une partie de nos échanges est gérée par une IA" in p
        assert "TIRAGE DE CARTES AVEC CONSENTEMENT" in p


def test_acute_distress_still_overrides_everything():
    import datetime
    user = dict(USER, dernier_signal_aigu_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    if A._signal_aigu_recent(user, fenetre_heures=24):
        p = A.get_system_prompt(user, "selena")
        assert "3114" in p and "Tu es Séléna" not in p


def test_first_turn_still_strips_competing_blocks():
    p = _p("ezra", premier_tour_post_onboarding=True)
    assert A.BLOC_PROFIL_AUTRE_PERSONNE not in p
    assert "Tu es Ezra" in p


def test_sketch_matches_app_text_exactly():
    # Mêmes phrases que test/personality_sketch_test.dart côté app.
    assert A.esquisse_personnalite("Taureau") == (
        "J’ai déjà quelques intuitions sur toi. Je te vois comme quelqu’un "
        "de fidèle, patient et attaché à ce qui est vrai."
    )
    assert "quelqu’un d’attentionné, précis et toujours là" in (
        A.esquisse_personnalite("Vierge")
    )
    assert len(A._ESQUISSES_SIGNE) == 12
    assert A.esquisse_personnalite("inconnu") == ""


def test_first_reply_knows_the_sketch_it_already_showed():
    user = dict(USER, signe_zodiaque="Taureau", chemin_de_vie="7")
    p = A.get_system_prompt(user, "selena", onboarding_profile_intro=True)
    assert A.esquisse_personnalite("Taureau") in p
    assert "Ne refais pas d'esquisse" in p
    assert "UNE question simple sur" in p


def test_discovery_phase_only_when_asked():
    assert "PHASE DÉCOUVERTE" not in _p()
    p = _p(phase_decouverte=True)
    assert "PHASE DÉCOUVERTE" in p
    assert "jamais un questionnaire" in p
    assert "qu'est-ce qui l'amène aujourd'hui" in p


def _maj(**kw):
    base = dict(statut="presented", feedback=None, signe="Taureau",
                deja_decrit="", intro_du_tour=True, message="")
    base.update(kw)
    return A.profil_depuis_reponse_esquisse(**base)


def test_personality_is_always_saved_after_the_sketch():
    oui = _maj(feedback="confirmed", message="oui")
    assert oui["onboarding_profile_status"] == "confirmed"
    assert "fidèle, patient et attaché à ce qui est vrai" in oui["profile_self_description"]
    ajout = _maj(message="Oui, et je suis aussi très têtue")
    assert "têtue" in ajout["profile_self_description"]
    assert "fidèle" in ajout["profile_self_description"]
    # Une vraie question n'est pas une description de soi.
    assert _maj(message="Est-ce qu'il va revenir ?") is None
    # Une correction reste gérée par le chemin « PROFIL À PRÉCISER ».
    assert _maj(feedback="corrected", message="non") is None
    # Déjà décrit : on ne réécrit pas, on confirme seulement.
    assert _maj(feedback="confirmed", deja_decrit="x") == {
        "onboarding_profile_status": "confirmed"
    }
    # « bonjour » n'est pas une description de soi.
    assert _maj(message="Bonjour !") is None
    # Hors tour d'accueil, une réponse libre ne devient pas le profil.
    assert _maj(message="bonjour", intro_du_tour=False) is None
