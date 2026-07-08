import sys
from unittest.mock import MagicMock

# Mock psycopg2 avant l'import de auryel_bot (pas de PostgreSQL en local)
sys.modules["psycopg2"] = MagicMock()

import os
os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("VERIFY_TOKEN", "test")
os.environ.setdefault("ADMIN_PASSWORD", "test")
os.environ.setdefault("CRON_SECRET", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("STRIPE_SK", "sk_test_placeholder")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_placeholder")
os.environ.setdefault("WHATSAPP_TOKEN", "test")
os.environ.setdefault("PHONE_NUMBER_ID", "test")
os.environ.setdefault("GROQ_API_KEY", "test")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("META_APP_SECRET", "test")
os.environ.setdefault("DAILY_SECRET", "test")
os.environ.setdefault("SEO_SECRET", "test")

from datetime import datetime
import hmac as hmac_mod
import hashlib
import json
from auryel_bot import get_system_prompt, GUIDES, app as flask_app

USER_BASE = {
    "phone": "+33600000000",
    "prenom": "Sarah",
    "nb_echanges": 8,
    "date_premier_contact": "2026-04-01T10:00:00",
    "date_dernier_contact": "2026-05-10T10:00:00",
    "niveau_attachement": 20,
    "niveau_detresse": 30,
    "theme_dominant": "amour",
    "douleur_principale": "abandon",
    "peur_dominante": "ne pas être aimée",
    "prenoms_importants": "Karim",
    "dernier_sujet_sensible": "",
    "derniere_intention": "",
    "depuis_site": True,
    "abonne": True,
    "guide": "selena",
    "nom_affiche": "Séléna",
    "email": "",
    "etat": "normal",
}

USER_CRISE = {
    **USER_BASE,
    "niveau_detresse": 100,
    "dernier_sujet_sensible": "suicide",
}

print("=" * 60)
print("TEST — 10 GUIDES")
print("=" * 60)

prompts = {}
for guide in ["selena", "luna", "myriam", "thea", "cassandre", "orion", "ezra", "kael", "raphael", "maia"]:
    user = {**USER_BASE, "guide": guide}
    prompt = get_system_prompt(user, guide)
    prompts[guide] = prompt
    print(f"\n{'─'*40}")
    print(f"GUIDE : {guide}")
    print(f"{'─'*40}")
    print(prompt[:600])

print("\n" + "=" * 60)
print("TEST SÉCURITÉ — cas suicide")
print("=" * 60)
prompt_crise = get_system_prompt(USER_CRISE, "selena")
print(prompt_crise)

print("\n" + "=" * 60)
print("VÉRIFICATIONS AUTOMATIQUES")
print("=" * 60)

valeurs = list(prompts.values())
tous_distincts = len(set(valeurs)) == len(valeurs)
print(f"{'✅' if tous_distincts else '❌'} Prompts distincts : {tous_distincts}")

blocs_interdits = ["TON IDENTITÉ", "Niveau de mysticisme", "guide spirituel sur la plateforme"]
securite_ok = all(bloc not in prompt_crise for bloc in blocs_interdits)
print(f"{'✅' if securite_ok else '❌'} Sécurité sans rôle mystique : {securite_ok}")

print("\n✅ Test terminé.")

# ── Tests webhook Meta ────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("TEST WEBHOOK META — Vérification signature X-Hub-Signature-256")
print("=" * 60)

# Payload minimal : value sans "messages" → retour 200 immédiat sans accès DB
_wh_payload = json.dumps({
    "entry": [{"changes": [{"value": {"statuses": []}}]}]
}).encode()
_TEST_SECRET = "test_meta_secret_webhook_abc"

def _sig(secret, body):
    return "sha256=" + hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()

with flask_app.test_client() as _wc:
    _wh_results = []

    # Cas 1 : META_APP_SECRET absent → 403 fail-closed
    os.environ.pop("META_APP_SECRET", None)
    r1 = _wc.post("/webhook", data=_wh_payload, content_type="application/json")
    ok1 = r1.status_code == 403
    print(f"{'✅' if ok1 else '❌'} SECRET absent → {r1.status_code} (attendu 403)")
    _wh_results.append(ok1)

    # Cas 2 : META_APP_SECRET présent, signature valide → 200
    os.environ["META_APP_SECRET"] = _TEST_SECRET
    r2 = _wc.post("/webhook", data=_wh_payload, content_type="application/json",
                  headers={"X-Hub-Signature-256": _sig(_TEST_SECRET, _wh_payload)})
    ok2 = r2.status_code == 200
    print(f"{'✅' if ok2 else '❌'} Signature valide → {r2.status_code} (attendu 200)")
    _wh_results.append(ok2)

    # Cas 3 : META_APP_SECRET présent, signature incorrecte → 403
    r3 = _wc.post("/webhook", data=_wh_payload, content_type="application/json",
                  headers={"X-Hub-Signature-256": "sha256=deadbeefcafebabe"})
    ok3 = r3.status_code == 403
    print(f"{'✅' if ok3 else '❌'} Signature invalide → {r3.status_code} (attendu 403)")
    _wh_results.append(ok3)

    # Cas 4 : META_APP_SECRET présent, header absent → 403
    r4 = _wc.post("/webhook", data=_wh_payload, content_type="application/json")
    ok4 = r4.status_code == 403
    print(f"{'✅' if ok4 else '❌'} Header absent → {r4.status_code} (attendu 403)")
    _wh_results.append(ok4)

    os.environ.pop("META_APP_SECRET", None)

webhook_ok = all(_wh_results)
print(f"\n{'✅' if webhook_ok else '❌'} Webhook Meta : {'PASS' if webhook_ok else 'FAIL'}")
if not webhook_ok:
    raise SystemExit(1)

# ── Test déduplication WAMID ──────────────────────────────────────────────────
print("\n" + "=" * 60)
print("TEST DÉDUPLICATION WAMID — même message_id envoyé deux fois")
print("=" * 60)

from unittest.mock import patch, MagicMock
from auryel_bot import insert_user_msg_dedup

_TEST_PHONE = "+33699000001"
_TEST_WAMID = "wamid.TESTDEDUP0001"
_TEST_CONTENT = "Bonjour, test dédup"

# Simule la table messages en mémoire avec un set de WAMIDs déjà insérés
_inserted_wamids = set()

def _fake_insert_dedup(phone, content, wamid):
    if wamid in _inserted_wamids:
        return False  # doublon
    _inserted_wamids.add(wamid)
    return True  # première insertion

# Première réception : doit retourner True
first = _fake_insert_dedup(_TEST_PHONE, _TEST_CONTENT, _TEST_WAMID)
ok_first = first is True
print(f"{'✅' if ok_first else '❌'} 1ère réception → inséré={first} (attendu True)")

# Deuxième réception (retry Meta) : doit retourner False
second = _fake_insert_dedup(_TEST_PHONE, _TEST_CONTENT, _TEST_WAMID)
ok_second = second is False
print(f"{'✅' if ok_second else '❌'} 2ème réception → inséré={second} (attendu False = doublon ignoré)")

# WAMID différent : doit être accepté
_TEST_WAMID2 = "wamid.TESTDEDUP0002"
third = _fake_insert_dedup(_TEST_PHONE, "Autre message", _TEST_WAMID2)
ok_third = third is True
print(f"{'✅' if ok_third else '❌'} WAMID différent → inséré={third} (attendu True)")

dedup_ok = ok_first and ok_second and ok_third
print(f"\n{'✅' if dedup_ok else '❌'} Déduplication WAMID : {'PASS' if dedup_ok else 'FAIL'}")
if not dedup_ok:
    raise SystemExit(1)

# ── Tests QW-1 : garde-fou détresse dans les 3 crons de relance ───────────────
print("\n" + "=" * 60)
print("TEST QW-1 — niveau_detresse >= 70 bloque toute relance")
print("=" * 60)

from datetime import timedelta
from auryel_bot import cron_daily, cron_morning, cron_relances_intraday

NOW_QW1 = datetime(2026, 6, 19, 14, 0, 0)

def make_full_user(**kwargs):
    base = {
        "phone": "+33611112222", "email": "", "prenom": "Sarah", "guide": "selena",
        "nom_affiche": "Séléna", "nb_echanges": 8, "dernier_outil": "",
        "date_premier_contact": (NOW_QW1 - timedelta(days=5)).isoformat(),
        "date_dernier_contact": (NOW_QW1 - timedelta(hours=5)).isoformat(),
        "etat": "normal", "abonne": False, "date_abonnement": "",
        "stripe_customer_id": "", "relance_j6_envoyee": False, "relance_j8_envoyee": False,
        "dernier_relance_abonne_at": "", "relance_abonne_count": 0,
        "dernier_rituel_date": "", "dernier_rituel_type": "", "depuis_site": True,
        "prenoms_importants": "", "theme_dominant": "", "douleur_principale": "",
        "peur_dominante": "", "niveau_detresse": 100, "niveau_attachement": 0,
        "dernier_sujet_sensible": "", "derniere_intention": "",
        "relance_j7_envoyee": False, "onboarding_step": "termine", "onboarding_done": True,
        "profil_initial": "", "onboarding_question": "", "onboarding_psaume": "",
        "derniere_relance_conversationnelle_at": "", "derniere_relance_conversationnelle_type": "",
        "relance_hebdo_envoyee": False, "derniere_relance_hebdo_at": "",
        "stop_relances": False, "derniere_verif_stripe_at": "",
        "last_morning_message_at": "", "last_template_message_at": "",
        "templates_sans_reponse": 0, "morning_messages_enabled": True,
        "free_entry_expires_at": "", "source_channel": "seo",
        "last_h4_relance_at": "", "last_h22_relance_at": "",
        "messages_today_count": 0, "messages_today_date": "",
        "categorie_principale": "besoin_guidance", "relances_ids_envoyes": "",
        "date_derniere_proposition_tirage": "", "nb_echanges_dernier_tirage": 0,
    }
    base.update(kwargs)
    return base

def _fake_conn(phone):
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = [(phone,)]
    conn.cursor.return_value = cur
    return conn

qw1_results = []

def _check(label, condition):
    qw1_results.append(condition)
    print(f"{'✅' if condition else '❌'} {label}")

# QW-1.1 — cron_daily, branche ABONNÉ, detresse=100 → aucune relance abonné envoyée
with patch("auryel_bot.get_conn", return_value=_fake_conn("+33611112222")), \
     patch("auryel_bot.get_user", return_value=make_full_user(abonne=True)), \
     patch("auryel_bot.get_nb_jours", return_value=5), \
     patch("auryel_bot.get_jours_absence", return_value=5), \
     patch("auryel_bot.send_message") as m_send, \
     patch("auryel_bot.send_template_message") as m_tpl, \
     patch("auryel_bot.log_event") as m_log, \
     patch("auryel_bot._cron_purge_anciens", return_value="skipped_test"), \
     patch("auryel_bot.DAILY_SECRET", "test"):
    with flask_app.test_client() as wc:
        r = wc.post("/cron/daily", json={"secret": "test"})
    _check("QW-1.1 daily/abonné : réponse 200", r.status_code == 200)
    _check("QW-1.1 daily/abonné : send_message jamais appelé (compteur=0)", not m_send.called)
    _check("QW-1.1 daily/abonné : log_event('skip_relance_detresse', branche=abonne) émis",
           any(c.args[:1] == ("skip_relance_detresse",) and c.kwargs.get("branche") == "abonne"
               for c in m_log.call_args_list))

# QW-1.2 — cron_daily, branche NON-ABONNÉ, detresse=100 → aucune relance J+2/3/4 envoyée
with patch("auryel_bot.get_conn", return_value=_fake_conn("+33611112222")), \
     patch("auryel_bot.get_user", return_value=make_full_user(abonne=False)), \
     patch("auryel_bot.get_nb_jours", return_value=5), \
     patch("auryel_bot.get_stripe_links", return_value={}) as m_links, \
     patch("auryel_bot.send_message") as m_send, \
     patch("auryel_bot.send_template_message") as m_tpl, \
     patch("auryel_bot.log_event") as m_log, \
     patch("auryel_bot._cron_purge_anciens", return_value="skipped_test"), \
     patch("auryel_bot.DAILY_SECRET", "test"):
    with flask_app.test_client() as wc:
        r = wc.post("/cron/daily", json={"secret": "test"})
    _check("QW-1.2 daily/non-abonné : réponse 200", r.status_code == 200)
    _check("QW-1.2 daily/non-abonné : send_message jamais appelé (compteur=0)", not m_send.called)
    _check("QW-1.2 daily/non-abonné : send_template_message jamais appelé", not m_tpl.called)
    _check("QW-1.2 daily/non-abonné : get_stripe_links jamais atteint (continue avant)", not m_links.called)
    _check("QW-1.2 daily/non-abonné : log_event('skip_relance_detresse', branche=non_abonne) émis",
           any(c.args[:1] == ("skip_relance_detresse",) and c.kwargs.get("branche") == "non_abonne"
               for c in m_log.call_args_list))

# QW-1.3 — cron_morning, detresse=100 → aucun template/message matin envoyé
with patch("auryel_bot.get_conn", return_value=_fake_conn("+33611112222")), \
     patch("auryel_bot.get_user", return_value=make_full_user()), \
     patch("auryel_bot.choose_morning_send_mode") as m_mode, \
     patch("auryel_bot.send_message") as m_send, \
     patch("auryel_bot.send_template_message") as m_tpl, \
     patch("auryel_bot.log_event") as m_log, \
     patch("auryel_bot.DAILY_SECRET", "test"):
    with flask_app.test_client() as wc:
        r = wc.post("/cron/morning", json={"secret": "test"})
    _check("QW-1.3 morning : réponse 200", r.status_code == 200)
    _check("QW-1.3 morning : choose_morning_send_mode jamais appelé (skip avant décision)", not m_mode.called)
    _check("QW-1.3 morning : send_message jamais appelé (compteur=0)", not m_send.called)
    _check("QW-1.3 morning : send_template_message jamais appelé (compteur=0)", not m_tpl.called)
    _check("QW-1.3 morning : log_event('skip_relance_detresse', cron=morning) émis",
           any(c.args[:1] == ("skip_relance_detresse",) and c.kwargs.get("cron") == "morning"
               for c in m_log.call_args_list))

# QW-1.4 — cron_relances_intraday, detresse=100, dans la fenêtre H+4 → aucune relance envoyée
# Le cron fait un early-return hors 8h-22h Paris : on fige l'horloge à 14h pour que le
# test soit déterministe quelle que soit l'heure réelle d'exécution.
import datetime as _dt_module

class _FrozenDateTime(_dt_module.datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return _dt_module.datetime(2026, 6, 19, 14, 0, 0, tzinfo=tz)
        return _dt_module.datetime(2026, 6, 19, 14, 0, 0)

with patch("auryel_bot.get_conn", return_value=_fake_conn("+33611112222")), \
     patch("auryel_bot.get_user", return_value=make_full_user(
         date_dernier_contact=(NOW_QW1 - timedelta(hours=5)).isoformat())), \
     patch("auryel_bot.choisir_message_relance") as m_choisir, \
     patch("auryel_bot.send_message") as m_send, \
     patch("auryel_bot.log_event") as m_log, \
     patch("auryel_bot.datetime", _FrozenDateTime):
    cron_relances_intraday()
    _check("QW-1.4 intraday : choisir_message_relance jamais appelé (skip avant décision)", not m_choisir.called)
    _check("QW-1.4 intraday : send_message jamais appelé (compteur=0)", not m_send.called)
    _check("QW-1.4 intraday : log_event('skip_relance_detresse', cron=intraday) émis",
           any(c.args[:1] == ("skip_relance_detresse",) and c.kwargs.get("cron") == "intraday"
               for c in m_log.call_args_list))

qw1_ok = all(qw1_results)
print(f"\n{'✅' if qw1_ok else '❌'} QW-1 (4 tests) : {'PASS' if qw1_ok else 'FAIL'}")
if not qw1_ok:
    raise SystemExit(1)

# ── Tests IA-1 (commit 2) : détection aiguë/fond + word-boundaries ─────────────
print("\n" + "=" * 60)
print("TEST IA-1 — word-boundaries + séparation signal aigu / niveau de fond")
print("=" * 60)

from auryel_bot import detecter_contexte_emotionnel

ia1_results = []

def _detect(message, detresse_depart=0):
    """Passe un message dans detecter_contexte_emotionnel sur un user neuf."""
    user = {"niveau_detresse": detresse_depart, "niveau_attachement": 0}
    return detecter_contexte_emotionnel(message, dict(user))

def _ia1(label, condition):
    ia1_results.append(condition)
    print(f"{'✅' if condition else '❌'} {label}")

# Faux positifs qui ne doivent PLUS scorer ni armer le signal aigu
for phrase in ["c'est normal", "seulement 5 min", "enfin je vais mieux",
               "à mourir de rire", "ce boulot va me tuer", "frapper à la porte"]:
    u = _detect(phrase)
    _ia1(f"« {phrase} » → niveau_detresse=0 (pas de score)", u.get("niveau_detresse") == 0)
    _ia1(f"« {phrase} » → signal_aigu=False", u.get("signal_aigu") is False)

# Vrais signaux aigus → signal_aigu=True + horodatage, SANS figer à 100
for phrase in ["je veux me suicider", "je veux mourir", "j'ai envie d'en finir"]:
    u = _detect(phrase, detresse_depart=50)
    _ia1(f"« {phrase} » → signal_aigu=True", u.get("signal_aigu") is True)
    _ia1(f"« {phrase} » → dernier_signal_aigu_at horodaté", bool(u.get("dernier_signal_aigu_at")))
    _ia1(f"« {phrase} » → niveau_detresse NON figé à 100 (anti-cliquet)",
         u.get("niveau_detresse") == 53)

# Vrai positif de fond (word-boundary) : « je me sens seule » score, timestamp maj posé
u = _detect("je me sens seule ce soir")
_ia1("« je me sens seule » → niveau_detresse=3 (fond)", u.get("niveau_detresse") == 3)
_ia1("« je me sens seule » → detresse_maj_at horodaté", bool(u.get("detresse_maj_at")))
_ia1("« je me sens seule » → signal_aigu=False (pas d'aigu)", u.get("signal_aigu") is False)

ia1_ok = all(ia1_results)
print(f"\n{'✅' if ia1_ok else '❌'} IA-1 ({len(ia1_results)} assertions) : {'PASS' if ia1_ok else 'FAIL'}")
if not ia1_ok:
    raise SystemExit(1)
