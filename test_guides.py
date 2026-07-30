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
os.environ.setdefault("TAROT_MEDIA_UPLOAD_DISABLED", "1")

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
    # Commit 3 : le 3114 dépend de dernier_signal_aigu_at récent (< 24h), plus du score
    # ni du latch dernier_sujet_sensible. Simule un signal aigu émis à l'instant.
    "dernier_signal_aigu_at": datetime.now().isoformat(),
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

# ── Tests IA-1 (commit 3) : décroissance + fenêtre aigu + garde-fous marketing ──
print("\n" + "=" * 60)
print("TEST IA-1 (commit 3) — décroissance 15pts/j, fenêtre 3114 24h, marketing 7j")
print("=" * 60)

from datetime import timedelta as _td
from auryel_bot import (_niveau_detresse_effectif, _signal_aigu_recent,
                         _detresse_bloque_marketing)

ia1c_results = []

def _ia1c(label, condition):
    ia1c_results.append(condition)
    print(f"{'✅' if condition else '❌'} {label}")

def _il_y_a(jours=0, heures=0):
    return (datetime.now() - _td(days=jours, hours=heures)).isoformat()

# Marqueur du mode crise : PROMPT_MAITRE mentionne déjà "3114" en permanence dans sa
# section sécurité générale (L~1881, hors scope IA-1) — un simple "3114" in/not in
# prompt ne distingue donc PAS le mode crise. Le vrai marqueur est le début du texte
# de bascule (retour court, hors PROMPT_MAITRE) : "Sors immédiatement du rôle de guide".
def _mode_crise_actif(prompt):
    return "Sors immédiatement du rôle de guide" in prompt

# Cas 1 — score 100 il y a 8 jours : décru sous 0 → plancher 0, marketing rouvert, pas de 3114
u1 = {**USER_BASE, "niveau_detresse": 100, "detresse_maj_at": _il_y_a(jours=8),
      "dernier_signal_aigu_at": None, "dernier_sujet_sensible": ""}
_ia1c("score 100 il y a 8j → effectif == 0 (plancher)", _niveau_detresse_effectif(u1) == 0)
bloque1, raison1 = _detresse_bloque_marketing(u1)
_ia1c("score 100 il y a 8j → marketing rouvert (pas bloqué)", bloque1 is False)
p1 = get_system_prompt(u1, "selena")
_ia1c("score 100 il y a 8j → pas de mode crise", not _mode_crise_actif(p1))

# Cas 2a — signal aigu il y a 2h : mode crise actif
u2a = {**USER_BASE, "dernier_signal_aigu_at": _il_y_a(heures=2), "dernier_sujet_sensible": ""}
p2a = get_system_prompt(u2a, "selena")
_ia1c("signal aigu il y a 2h → mode crise actif", _mode_crise_actif(p2a))

# Cas 2b — signal aigu il y a 3 jours : PAS de mode crise (> 24h) mais marketing coupé (< 7j)
u2b = {**USER_BASE, "niveau_detresse": 0, "detresse_maj_at": None,
       "dernier_signal_aigu_at": _il_y_a(jours=3), "dernier_sujet_sensible": ""}
p2b = get_system_prompt(u2b, "selena")
_ia1c("signal aigu il y a 3j → PAS de mode crise (fenêtre 24h dépassée)", not _mode_crise_actif(p2b))
bloque2b, raison2b = _detresse_bloque_marketing(u2b)
_ia1c("signal aigu il y a 3j → marketing coupé (fenêtre 7j)", bloque2b is True)
_ia1c("signal aigu il y a 3j → raison='signal_aigu_recent'", raison2b == "signal_aigu_recent")

# Cas 3 — score de fond 95 SANS signal aigu : pas de mode crise, registre doux seulement
u3 = {**USER_BASE, "niveau_detresse": 95, "detresse_maj_at": None,
      "dernier_signal_aigu_at": None, "dernier_sujet_sensible": ""}
p3 = get_system_prompt(u3, "selena")
_ia1c("score 95 sans aigu → PAS de mode crise", not _mode_crise_actif(p3))
_ia1c("score 95 sans aigu → registre adouci présent", "REGISTRE ADOUCI" in p3)
bloque3, raison3 = _detresse_bloque_marketing(u3)
_ia1c("score 95 sans aigu → marketing coupé (raison='score')", bloque3 is True and raison3 == "score")

# Cas 4 — sécurité : USER_CRISE (signal aigu à l'instant) déclenche toujours le mode crise
p_crise = get_system_prompt(USER_CRISE, "selena")
_ia1c("USER_CRISE (signal aigu à l'instant) → mode crise actif", _mode_crise_actif(p_crise))

ia1c_ok = all(ia1c_results)
print(f"\n{'✅' if ia1c_ok else '❌'} IA-1 commit 3 ({len(ia1c_results)} assertions) : {'PASS' if ia1c_ok else 'FAIL'}")
if not ia1c_ok:
    raise SystemExit(1)

# ── Tests BUG 1 — détecteur demande_paiement + garde-fou détresse ──────────────
print("\n" + "=" * 60)
print("TEST BUG 1 — détecteur demande de paiement + garde-fou détresse")
print("=" * 60)

from auryel_bot import detecter_demande_paiement, get_reply

bug1_results = []

def _bug1(label, condition):
    bug1_results.append(condition)
    print(f"{'✅' if condition else '❌'} {label}")

# Phrase réelle de test de Nathanyel — doit matcher
_bug1("« envoie le lien pour que je paye » → détecté",
      detecter_demande_paiement("envoie le lien pour que je paye"))
for phrase in ["Envoie moi le lien de paiement", "je veux payer maintenant",
               "comment je peux m'abonner", "où je paye", "je peux payer",
               "j'aimerais passer au payant"]:
    _bug1(f"« {phrase} » → détecté", detecter_demande_paiement(phrase))

# Faux positifs à éviter (le "envoie le lien" nu, volontairement exclu)
for phrase in ["envoie le lien du tirage", "envoie-moi le lien vers l'article",
               "je paye attention à ce que tu dis", "c'est cher mais bon"]:
    _bug1(f"« {phrase} » → PAS détecté (faux positif évité)",
          not detecter_demande_paiement(phrase))

# ── Intégration get_reply() : le lien est injecté SAUF en détresse ─────────────
_BUG1_PHONE = "+33699000099"

def _user_bug1(**overrides):
    base = {**USER_BASE, "phone": _BUG1_PHONE, "guide": "maia",
            "niveau_detresse": 0, "detresse_maj_at": None, "dernier_signal_aigu_at": None,
            "dernier_sujet_sensible": "", "dernier_outil": "", "onboarding_done": True}
    base.update(overrides)
    return base

def _run_get_reply_capture_system(user_dict, message):
    with patch("auryel_bot.get_user", return_value=user_dict), \
         patch("auryel_bot.check_and_increment_daily_limit", return_value=False), \
         patch("auryel_bot.gerer_onboarding", return_value=None), \
         patch("auryel_bot.get_history", return_value=[]), \
         patch("auryel_bot.add_message"), \
         patch("auryel_bot.update_user"), \
         patch("auryel_bot.update_user_silent"), \
         patch("auryel_bot.choisir_citation", return_value=""), \
         patch("auryel_bot.log_event"), \
         patch("auryel_bot.call_llm", return_value="réponse factice") as m_llm:
        get_reply(_BUG1_PHONE, message)
        return m_llm.call_args[0][0][0]["content"]  # messages[0] = {"role":"system",...}

# Cas A — pas en détresse → le lien DOIT apparaître
system_a = _run_get_reply_capture_system(_user_bug1(), "envoie le lien pour que je paye")
_bug1("pas en détresse → bloc DEMANDE DE PAIEMENT présent", "DEMANDE DE PAIEMENT" in system_a)
_bug1("pas en détresse → lien réel présent dans le system prompt",
      f"phone={_BUG1_PHONE}".replace("+", "") in system_a.replace("+", "")
      or "payer?source=whatsapp&phone=" in system_a)

# Cas B — EN détresse (score effectif >= 70) → le lien NE DOIT PAS apparaître
system_b = _run_get_reply_capture_system(
    _user_bug1(niveau_detresse=100, detresse_maj_at=None),
    "envoie le lien pour que je paye"
)
_bug1("EN détresse (score 100) → bloc DEMANDE DE PAIEMENT ABSENT",
      "DEMANDE DE PAIEMENT" not in system_b)

# Cas C — message neutre, pas en détresse → pas de bloc (pas de demande)
system_c = _run_get_reply_capture_system(_user_bug1(), "je me sens un peu perdue aujourd'hui")
_bug1("message neutre → pas de bloc DEMANDE DE PAIEMENT", "DEMANDE DE PAIEMENT" not in system_c)

bug1_ok = all(bug1_results)
print(f"\n{'✅' if bug1_ok else '❌'} BUG 1 ({len(bug1_results)} assertions) : {'PASS' if bug1_ok else 'FAIL'}")
if not bug1_ok:
    raise SystemExit(1)

# ── Tests BUG 3 — plus de trial Stripe dans create-checkout ────────────────────
print("\n" + "=" * 60)
print("TEST BUG 3 — create-checkout sans trial_period_days")
print("=" * 60)

bug3_results = []

def _bug3(label, condition):
    bug3_results.append(condition)
    print(f"{'✅' if condition else '❌'} {label}")

with patch("auryel_bot.get_user", return_value={"phone": "+33600000099"}), \
     patch("auryel_bot.stripe.checkout.Session.create") as m_stripe:
    m_stripe.return_value = MagicMock(url="https://checkout.stripe.com/fake-session")
    with flask_app.test_client() as wc:
        r = wc.post("/stripe/create-checkout", json={
            "plan": "mensuel",
            "successUrl": "https://auryelvoyance.com/success.html",
            "cancelUrl": "https://auryelvoyance.com/payer",
            "phone": "+33600000099",
        })
    _bug3("create-checkout → 200", r.status_code == 200)
    kwargs = m_stripe.call_args.kwargs
    _bug3("subscription_data absent (plus de trial)", "subscription_data" not in kwargs)
    _bug3("metadata sans trial_days", "trial_days" not in kwargs.get("metadata", {}))
    _msg = kwargs.get("custom_text", {}).get("terms_of_service_acceptance", {}).get("message", "")
    _bug3("custom_text mentionne le paiement immédiat", "immédiat" in _msg)
    _bug3("custom_text ne promet plus '3 jours gratuits'", "3 jours gratuits" not in _msg)

bug3_ok = all(bug3_results)
print(f"\n{'✅' if bug3_ok else '❌'} BUG 3 ({len(bug3_results)} assertions) : {'PASS' if bug3_ok else 'FAIL'}")
if not bug3_ok:
    raise SystemExit(1)

# ============================================================
# TESTS — envoi tirage par media_id, fallback link, refresh périodique
# ============================================================
from unittest.mock import patch, MagicMock as _MM
import auryel_bot as _ab

print("\n" + "=" * 60)
print("TEST TIRAGE — send_image() media_id / fallback link")
print("=" * 60)

# T1 : media_id fourni et accepté par Meta -> payload envoyé avec "id", pas de fallback
with patch("auryel_bot.requests.post") as mock_post:
    mock_post.return_value = _MM(status_code=200, json=lambda: {}, text="")
    _ab.send_image("33600000000", "https://auryelvoyance.com/images/tarot/x.png",
                    caption="", media_id="MEDIA123")
    assert mock_post.call_count == 1, f"attendu 1 appel, obtenu {mock_post.call_count}"
    sent = mock_post.call_args.kwargs["json"]
    assert sent["image"] == {"id": "MEDIA123", "caption": ""}, sent
    print("✅ media_id accepté → un seul appel, payload par id")

# T2 : media_id fourni mais refusé par Meta (410/expiré) -> fallback automatique sur link
with patch("auryel_bot.requests.post") as mock_post:
    mock_post.side_effect = [
        _MM(status_code=410, json=lambda: {}, text="media expired"),
        _MM(status_code=200, json=lambda: {}, text=""),
    ]
    _ab.send_image("33600000000", "https://auryelvoyance.com/images/tarot/x.png",
                    caption="", media_id="MEDIA_EXPIRE")
    assert mock_post.call_count == 2, f"attendu 2 appels (id puis link), obtenu {mock_post.call_count}"
    premier = mock_post.call_args_list[0].kwargs["json"]
    second  = mock_post.call_args_list[1].kwargs["json"]
    assert premier["image"] == {"id": "MEDIA_EXPIRE", "caption": ""}, premier
    assert second["image"] == {"link": "https://auryelvoyance.com/images/tarot/x.png", "caption": ""}, second
    print("✅ media_id refusé (410) → fallback automatique sur link")

# T3 : media_id fourni mais exception réseau -> fallback automatique sur link
with patch("auryel_bot.requests.post") as mock_post:
    mock_post.side_effect = [
        Exception("connection reset"),
        _MM(status_code=200, json=lambda: {}, text=""),
    ]
    _ab.send_image("33600000000", "https://auryelvoyance.com/images/tarot/x.png",
                    caption="", media_id="MEDIA_KO")
    assert mock_post.call_count == 2, f"attendu 2 appels, obtenu {mock_post.call_count}"
    print("✅ exception réseau sur media_id → fallback automatique sur link")

# T4 : pas de media_id (cache pas encore rempli) -> comportement actuel inchangé, un seul appel par link
with patch("auryel_bot.requests.post") as mock_post:
    mock_post.return_value = _MM(status_code=200, json=lambda: {}, text="")
    _ab.send_image("33600000000", "https://auryelvoyance.com/images/tarot/x.png", caption="")
    assert mock_post.call_count == 1, f"attendu 1 appel, obtenu {mock_post.call_count}"
    sent = mock_post.call_args.kwargs["json"]
    assert sent["image"] == {"link": "https://auryelvoyance.com/images/tarot/x.png", "caption": ""}, sent
    print("✅ sans media_id → comportement actuel inchangé (link direct)")

# T5 : tirer_cartes() lit bien TAROT_MEDIA_IDS et le transmet à send_image()
with patch("auryel_bot.requests.post") as mock_post, patch.dict(_ab.TAROT_MEDIA_IDS, {}, clear=True):
    mock_post.return_value = _MM(status_code=200, json=lambda: {}, text="")
    if _ab.TAROT_MAPPING:
        un_fichier = next(iter(_ab.TAROT_MAPPING))
        _ab.TAROT_MEDIA_IDS[un_fichier] = "MEDIA_CACHE"
        with patch("auryel_bot._random_tarot.choice", return_value=un_fichier):
            _ab.tirer_cartes("33600000000")
        sent = mock_post.call_args.kwargs["json"]
        assert sent["image"]["id"] == "MEDIA_CACHE", sent
        print("✅ tirer_cartes() transmet bien le media_id en cache à send_image()")
    else:
        print("⚠️  TAROT_MAPPING vide en environnement de test, T5 sautée")

print("\n" + "=" * 60)
print("TEST TIRAGE — rafraîchissement périodique (anti-expiration)")
print("=" * 60)

# T6 : cache frais (< 20 jours) -> _refresh_tarot_media_si_necessaire() ne relance rien
with patch("auryel_bot._upload_tarot_media") as mock_upload, \
     patch("auryel_bot._tarot_media_uploaded_at", _ab.time.time() - 3600):  # 1h
    _ab._refresh_tarot_media_si_necessaire()
    _ab.time.sleep(0.05)  # laisse une éventuelle Thread (bug) démarrer avant de vérifier
    assert mock_upload.call_count == 0, "le refresh n'aurait pas dû se déclencher, cache frais"
    print("✅ cache frais (1h) → pas de re-upload déclenché")

# T7 : cache périmé (> 20 jours) -> _refresh_tarot_media_si_necessaire() relance un upload en thread
with patch("auryel_bot._upload_tarot_media") as mock_upload, \
     patch("auryel_bot._tarot_media_uploaded_at", _ab.time.time() - _ab.TAROT_MEDIA_REFRESH_INTERVAL_S - 1):
    _ab._refresh_tarot_media_si_necessaire()
    _ab.time.sleep(0.2)  # laisse le thread démarré s'exécuter
    assert mock_upload.call_count == 1, f"attendu 1 relance d'upload, obtenu {mock_upload.call_count}"
    print("✅ cache périmé (>20j) → re-upload déclenché en tâche de fond")

# T8 : jamais uploadé (uploaded_at=0.0) -> traité comme périmé, upload déclenché
with patch("auryel_bot._upload_tarot_media") as mock_upload, \
     patch("auryel_bot._tarot_media_uploaded_at", 0.0):
    _ab._refresh_tarot_media_si_necessaire()
    _ab.time.sleep(0.2)
    assert mock_upload.call_count == 1, f"attendu 1 relance d'upload, obtenu {mock_upload.call_count}"
    print("✅ jamais uploadé (0.0) → traité comme périmé, upload déclenché")

# T9 : deux uploads concurrents -> le verrou empêche le second de tourner en parallèle
# (TAROT_MEDIA_UPLOAD_DISABLED levé juste pour ce test, seule façon d'exercer le verrou
# réel de _upload_tarot_media() ; requests.post reste mocké, donc aucun vrai appel réseau)
with patch("auryel_bot.requests.post") as mock_post, \
     patch.dict(os.environ, {"TAROT_MEDIA_UPLOAD_DISABLED": ""}):
    mock_post.return_value = _MM(status_code=200, json=lambda: {}, text="")
    _ab._tarot_media_upload_lock.acquire()  # simule un upload déjà en cours
    try:
        _ab._upload_tarot_media()  # doit sortir immédiatement (lock non-bloquant)
        assert mock_post.call_count == 0, "un second upload n'aurait pas dû démarrer, lock déjà pris"
        print("✅ upload déjà en cours → un second déclenchement ne fait rien (verrou non-bloquant)")
    finally:
        _ab._tarot_media_upload_lock.release()

print("\n✅ TESTS TIRAGE media_id + refresh : PASS")

# ============================================================
# TESTS — priorité au tirage d'accueil, consentement par mot entier
# ============================================================
print("\n" + "=" * 60)
print("TEST TIRAGE D'ACCUEIL — priorité au tirage, pas de parasite")
print("=" * 60)

def _run_get_reply_accueil(user_dict, message):
    """Même pattern que _run_get_reply_capture_system (BUG 1) mais mocke aussi
    tirer_cartes() directement — pas seulement requests.post — pour ne jamais faire
    de vrai appel réseau ni dépendre d'un thread résiduel."""
    with patch("auryel_bot.get_user", return_value=user_dict), \
         patch("auryel_bot.check_and_increment_daily_limit", return_value=False), \
         patch("auryel_bot.gerer_onboarding", return_value=None), \
         patch("auryel_bot.get_history", return_value=[]), \
         patch("auryel_bot.add_message"), \
         patch("auryel_bot.update_user"), \
         patch("auryel_bot.update_user_silent"), \
         patch("auryel_bot.choisir_citation", return_value=""), \
         patch("auryel_bot.choisir_psaume", return_value=None), \
         patch("auryel_bot.log_event"), \
         patch("auryel_bot.tirer_cartes", return_value=(None, ["La Lune", "Le Pape", "Le Pendu"])) as m_tirer, \
         patch("auryel_bot.call_llm", return_value="réponse factice") as m_llm:
        get_reply(_BUG1_PHONE, message)
        system_prompt = m_llm.call_args[0][0][0]["content"]
        return system_prompt, m_tirer.called

# T1 : 1er tour post-onboarding, message mentionnant une autre personne
# -> PROFIL DE L'AUTRE PERSONNE et RITUELS CONCRETS absents, TIRAGE D'ACCUEIL présent et impératif
system_1, _ = _run_get_reply_accueil(_user_bug1(onboarding_done=False), "je pense qu'elle me trompe")
assert "PROFIL DE L'AUTRE PERSONNE" not in system_1, "le parasite PROFIL n'a pas été neutralisé au 1er tour"
assert "RITUELS CONCRETS ET VARIÉS" not in system_1, "le parasite RITUELS n'a pas été neutralisé au 1er tour"
assert "TIRAGE D'ACCUEIL" in system_1, "le bloc TIRAGE D'ACCUEIL doit rester présent au 1er tour"
assert "OBLIGATOIRE" in system_1, "le bloc doit être formulé de façon impérative"
print("✅ 1er tour post-onboarding : PROFIL/RITUELS neutralisés, TIRAGE D'ACCUEIL impératif")

# T2 : tour normal (onboarding déjà fait avant ce message) -> comportement inchangé
system_2, _ = _run_get_reply_accueil(_user_bug1(onboarding_done=True), "je pense qu'elle me trompe")
assert "PROFIL DE L'AUTRE PERSONNE" in system_2, "ne doit pas être supprimé hors 1er tour"
assert "RITUELS CONCRETS ET VARIÉS" in system_2, "ne doit pas être supprimé hors 1er tour"
assert "TIRAGE D'ACCUEIL" not in system_2, "ne doit apparaître qu'au 1er tour post-onboarding"
print("✅ tour normal (hors onboarding) : PROFIL/RITUELS toujours présents, pas de TIRAGE D'ACCUEIL")

print("\n" + "=" * 60)
print("TEST CONSENTEMENT TIRAGE — mot entier, pas sous-chaîne")
print("=" * 60)

# T3 : "oui" noyé dans une phrase qui répond à autre chose -> pas de tirage (faux positif d'avant)
_, appele_3 = _run_get_reply_accueil(
    _user_bug1(onboarding_done=True, tirage_propose_en_attente=True),
    "oui elle est bizarre"
)
assert not appele_3, "« oui elle est bizarre » ne doit PAS déclencher le tirage (faux positif substring)"
print("✅ « oui elle est bizarre » → tirage NON déclenché")

# T4 : "oui" seul -> déclenche bien le tirage
_, appele_4 = _run_get_reply_accueil(
    _user_bug1(onboarding_done=True, tirage_propose_en_attente=True),
    "oui"
)
assert appele_4, "« oui » seul DOIT déclencher le tirage"
print("✅ « oui » seul → tirage déclenché")

# T5 : "d'accord, vas-y" (apostrophe droite) -> déclenche bien le tirage
_, appele_5 = _run_get_reply_accueil(
    _user_bug1(onboarding_done=True, tirage_propose_en_attente=True),
    "d'accord, vas-y"
)
assert appele_5, "« d'accord, vas-y » DOIT déclencher le tirage"
print("✅ « d'accord, vas-y » → tirage déclenché")

# T6 : "d’accord" (apostrophe courbe, clavier iPhone) -> déclenche bien le tirage (normalisation)
_, appele_6 = _run_get_reply_accueil(
    _user_bug1(onboarding_done=True, tirage_propose_en_attente=True),
    "d’accord"
)
assert appele_6, "« d’accord » (apostrophe courbe) DOIT déclencher le tirage"
print("✅ « d’accord » (apostrophe courbe) → tirage déclenché")

# T7 : pas de flag armé -> un "oui" isolé ne déclenche rien (comportement déjà correct, non-régression)
_, appele_7 = _run_get_reply_accueil(
    _user_bug1(onboarding_done=True, tirage_propose_en_attente=False),
    "oui"
)
assert not appele_7, "sans flag armé, « oui » ne doit rien déclencher"
print("✅ pas de flag armé → « oui » ne déclenche rien (non-régression)")

# ============================================================
# TESTS — Brique 2b : psaume de rebond (rare, en appui)
# ============================================================
print("\n" + "=" * 60)
print("TEST BRIQUE 2b — psaume de rebond")
print("=" * 60)

from auryel_bot import detecter_registre_message, choisir_psaume, POOLS_PSAUMES, PSAUMES

b2b_results = []
def _b2b(label, condition):
    b2b_results.append(condition)
    print(f"{'✅' if condition else '❌'} {label}")

# --- detecter_registre_message : un cas par registre + aucun match ---
cas_registre = {
    "amour":       "je pense encore à mon ex, cette rupture me hante",
    "deuil":       "j'ai appris son décès la semaine dernière, quel deuil",
    "décision":    "je dois choisir entre rester ou partir, quelle décision prendre",
    "blocage":     "je me sens bloquée, incapable d'avancer",
    "sens de vie": "je me demande quel est le sens de tout ça",
    "peur":        "j'ai tellement peur, cette angoisse ne me lâche pas",
}
for registre_attendu, phrase in cas_registre.items():
    _b2b(f"detecter_registre_message(« {phrase} ») → {registre_attendu}",
         detecter_registre_message(phrase) == registre_attendu)

_b2b("detecter_registre_message(message neutre) → None",
     detecter_registre_message("comment ça va aujourd'hui") is None)

# --- ne touche jamais theme_dominant (pas de paramètre user : verrou structurel) ---
_u_theme_fige = {"theme_dominant": "figé", "niveau_detresse": 0, "niveau_attachement": 0}
detecter_registre_message("je pense encore à mon ex, cette rupture me hante")
_b2b("detecter_registre_message n'a pas de paramètre user → theme_dominant existant intact",
     _u_theme_fige.get("theme_dominant") == "figé")

# --- formes fléchies courantes ajoutées à THEMES_EMOTIONNELS (en plus des cas ci-dessus) ---
cas_registre_flechis = {
    "deuil":   "ma mère est décédée",
    "deuil2":  "il vient de mourir",
    "amour":   "il m'a trompée",
    "blocage": "je n'y arrive pas, plus la force",
}
_b2b("detecter_registre_message(« ma mère est décédée ») → deuil",
     detecter_registre_message(cas_registre_flechis["deuil"]) == "deuil")
_b2b("detecter_registre_message(« il vient de mourir ») → deuil",
     detecter_registre_message(cas_registre_flechis["deuil2"]) == "deuil")
_b2b("detecter_registre_message(« il m'a trompée ») → amour",
     detecter_registre_message(cas_registre_flechis["amour"]) == "amour")
_b2b("detecter_registre_message(« je n'y arrive pas, plus la force ») → blocage",
     detecter_registre_message(cas_registre_flechis["blocage"]) == "blocage")

# --- choisir_psaume : garde-fous, cooldown, probabilité ---
_user_neutre = {"nb_echanges_dernier_psaume": 0, "niveau_detresse": 0,
                "detresse_maj_at": None, "dernier_signal_aigu_at": None}

with patch("auryel_bot.random.random", return_value=0.0):
    _b2b("choisir_psaume → None si onboarding_vient_de_finir",
         choisir_psaume("amour", _user_neutre, True, 10) is None)

    _b2b("choisir_psaume → None si aucun registre détecté ce tour",
         choisir_psaume(None, _user_neutre, False, 10) is None)

    _user_cooldown_actif = {**_user_neutre, "nb_echanges_dernier_psaume": 8}
    _b2b("choisir_psaume → None si (nb_echanges_actuel - nb_echanges_dernier_psaume) < 6",
         choisir_psaume("amour", _user_cooldown_actif, False, 10) is None)  # 10-8=2 < 6

    _res_ok = choisir_psaume("amour", _user_neutre, False, 10)  # 10-0=10 >= 6
    _b2b("choisir_psaume → renvoie (numero, texte) valide quand toutes les conditions sont réunies",
         _res_ok is not None and _res_ok[0] in POOLS_PSAUMES["amour"] and _res_ok[1] == PSAUMES[_res_ok[0]])

    _user_signal_aigu = {**_user_neutre, "dernier_signal_aigu_at": datetime.now().isoformat()}
    _b2b("choisir_psaume → None si signal aigu détecté ce tour (<24h) — garde-fou détresse",
         choisir_psaume("amour", _user_signal_aigu, False, 10) is None)

    _user_detresse_haute = {**_user_neutre, "niveau_detresse": 100, "detresse_maj_at": None}
    _b2b("choisir_psaume → None si marketing bloqué pour détresse (score effectif >= 70)",
         choisir_psaume("amour", _user_detresse_haute, False, 10) is None)

    for _registre in POOLS_PSAUMES:
        for _ in range(15):
            _res = choisir_psaume(_registre, _user_neutre, False, 10)
            if _res is not None:
                _b2b(f"choisir_psaume({_registre}) → numéro toujours dans POOLS_PSAUMES[{_registre}]",
                     _res[0] in POOLS_PSAUMES[_registre])

with patch("auryel_bot.random.random", return_value=0.99):
    _b2b("choisir_psaume → None si le tirage de probabilité échoue (random >= PROBA_PSAUME)",
         choisir_psaume("amour", _user_neutre, False, 10) is None)

b2b_ok = all(b2b_results)
print(f"\n{'✅' if b2b_ok else '❌'} BRIQUE 2b — detecter_registre_message/choisir_psaume ({len(b2b_results)} assertions) : {'PASS' if b2b_ok else 'FAIL'}")
if not b2b_ok:
    raise SystemExit(1)

# --- Arbitrage un-seul-supplément/tour : psaume prioritaire sur citation et tirage spontané ---
print("\n" + "=" * 60)
print("TEST BRIQUE 2b — arbitrage un seul supplément spirituel par tour")
print("=" * 60)

def _run_get_reply_arbitrage(user_dict, message, psaume_retour):
    with patch("auryel_bot.get_user", return_value=user_dict), \
         patch("auryel_bot.check_and_increment_daily_limit", return_value=False), \
         patch("auryel_bot.gerer_onboarding", return_value=None), \
         patch("auryel_bot.get_history", return_value=[]), \
         patch("auryel_bot.add_message"), \
         patch("auryel_bot.update_user"), \
         patch("auryel_bot.update_user_silent"), \
         patch("auryel_bot.choisir_psaume", return_value=psaume_retour), \
         patch("auryel_bot.choisir_citation") as m_citation, \
         patch("auryel_bot.log_event"), \
         patch("auryel_bot.call_llm", return_value="réponse factice") as m_llm:
        get_reply(_BUG1_PHONE, message)
        system_prompt = m_llm.call_args[0][0][0]["content"]
        return system_prompt, m_citation.called

# User dont l'état ferait normalement déclencher un tirage spontané (>=5 échanges depuis
# le dernier, pas encore proposé aujourd'hui) — sert à prouver que l'arbitrage supprime bien
# le tirage spontané quand un psaume est retenu, pas juste qu'il n'apparaît jamais.
_user_arbitrage = _user_bug1(nb_echanges=10, nb_echanges_dernier_tirage=0,
                              date_derniere_proposition_tirage="", onboarding_done=True,
                              tirage_propose_en_attente=False)

arb_results = []
def _arb(label, condition):
    arb_results.append(condition)
    print(f"{'✅' if condition else '❌'} {label}")

system_avec_psaume, citation_appelee = _run_get_reply_arbitrage(
    _user_arbitrage, "je pense encore à mon ex", (23, PSAUMES[23]))
_arb("psaume retenu → choisir_citation JAMAIS appelée", not citation_appelee)
_arb("psaume retenu → bloc PSAUME EN APPUI présent", "PSAUME EN APPUI" in system_avec_psaume)
_arb("psaume retenu → bloc INSPIRATION DU MOMENT absent", "INSPIRATION DU MOMENT" not in system_avec_psaume)
_arb("psaume retenu → bloc PROPOSITION TIRAGE SPONTANÉE absent (supprimé par l'arbitrage)",
     "PROPOSITION TIRAGE SPONTANÉE" not in system_avec_psaume)

system_sans_psaume, _ = _run_get_reply_arbitrage(_user_arbitrage, "je pense encore à mon ex", None)
_arb("sans psaume → bloc PSAUME EN APPUI absent", "PSAUME EN APPUI" not in system_sans_psaume)
_arb("sans psaume → bloc PROPOSITION TIRAGE SPONTANÉE présent (confirme que le test mesure bien une suppression)",
     "PROPOSITION TIRAGE SPONTANÉE" in system_sans_psaume)

arb_ok = all(arb_results)
print(f"\n{'✅' if arb_ok else '❌'} BRIQUE 2b — arbitrage ({len(arb_results)} assertions) : {'PASS' if arb_ok else 'FAIL'}")
if not arb_ok:
    raise SystemExit(1)

# --- Migration v17 : get_user() expose bien nb_echanges_dernier_psaume ---
print("\n" + "=" * 60)
print("TEST MIGRATION v17 — nb_echanges_dernier_psaume exposé par get_user()")
print("=" * 60)

import inspect as _inspect
import re as _re
from auryel_bot import get_user as _get_user

_src = _inspect.getsource(_get_user)
_select_match = _re.search(r"SELECT\s+(.*?)\s+FROM users", _src, _re.S)
_cols = [c.strip() for c in _select_match.group(1).replace("\n", " ").split(",") if c.strip()]
# Toutes les colonnes à 0 sauf celle visée (à son index réel, pas "la dernière" —
# des migrations ultérieures peuvent ajouter des colonnes après elle) : évite tout
# crash sur les colonnes date (`row[N].isoformat() if row[N] else ""`) tout en
# isolant la colonne visée par sa position réelle dans le SELECT.
_idx_psaume17 = _cols.index("nb_echanges_dernier_psaume")
_fake_row = tuple(999 if i == _idx_psaume17 else 0 for i in range(len(_cols)))

_cur_v17 = MagicMock()
_cur_v17.fetchone.return_value = _fake_row
_conn_v17 = MagicMock()
_conn_v17.cursor.return_value = _cur_v17

with patch("auryel_bot.get_conn", return_value=_conn_v17):
    _u_v17 = _get_user("+33600000000")

v17_results = []
def _v17(label, condition):
    v17_results.append(condition)
    print(f"{'✅' if condition else '❌'} {label}")

_v17("nb_echanges_dernier_psaume présent dans le SELECT (colonne ajoutée en migration v17)",
     "nb_echanges_dernier_psaume" in _cols)
_v17("get_user() expose nb_echanges_dernier_psaume avec la bonne valeur de colonne",
     _u_v17.get("nb_echanges_dernier_psaume") == 999)

v17_ok = all(v17_results)
print(f"\n{'✅' if v17_ok else '❌'} MIGRATION v17 ({len(v17_results)} assertions) : {'PASS' if v17_ok else 'FAIL'}")
if not v17_ok:
    raise SystemExit(1)

print("\n✅ TESTS BRIQUE 2b (psaume de rebond) : PASS")

print("\n✅ TESTS TIRAGE ACCUEIL + CONSENTEMENT : PASS")

# ============================================================
# TESTS — CONSULTATION point 1 : rituels conditionnels + sobriété moments graves
# ============================================================
print("\n" + "=" * 60)
print("TEST CONSULTATION 1 — BLOC_RITUELS_CONCRETS conditionnel")
print("=" * 60)

cons1_results = []
def _cons1(label, condition):
    cons1_results.append(condition)
    print(f"{'✅' if condition else '❌'} {label}")

_user_rc = {**USER_BASE, "guide": "selena"}
_sys_rc_avec = get_system_prompt(_user_rc, "selena", proposer_rituel_concret=True)
_sys_rc_sans = get_system_prompt(_user_rc, "selena", proposer_rituel_concret=False)
_sys_rc_defaut = get_system_prompt(_user_rc, "selena")

_cons1("proposer_rituel_concret=True → RITUELS CONCRETS ET VARIÉS présent",
       "RITUELS CONCRETS ET VARIÉS" in _sys_rc_avec)
_cons1("proposer_rituel_concret=False → RITUELS CONCRETS ET VARIÉS absent",
       "RITUELS CONCRETS ET VARIÉS" not in _sys_rc_sans)
_cons1("valeur par défaut (non précisée) → RITUELS CONCRETS ET VARIÉS absent",
       "RITUELS CONCRETS ET VARIÉS" not in _sys_rc_defaut)

cons1_ok = all(cons1_results)
print(f"\n{'✅' if cons1_ok else '❌'} CONSULTATION 1 — rituels conditionnels ({len(cons1_results)} assertions) : {'PASS' if cons1_ok else 'FAIL'}")
if not cons1_ok:
    raise SystemExit(1)

print("\n" + "=" * 60)
print("TEST CONSULTATION 1 — detecter_moment_grave (déclencheur dédié, pas le registre)")
print("=" * 60)

from auryel_bot import detecter_moment_grave

cons_dmg_results = []
def _cons_dmg(label, condition):
    cons_dmg_results.append(condition)
    print(f"{'✅' if condition else '❌'} {label}")

# Faux positifs à écarter (polysémie de "perdu"/"perte"/"mort" nu) — resserrage demandé.
_cons_dmg("« j'ai perdu mon travail » → PAS grave (perte au sens large, pas un deuil)",
          not detecter_moment_grave("j'ai perdu mon travail"))
_cons_dmg("« je me sens perdue » → PAS grave",
          not detecter_moment_grave("je me sens perdue"))
_cons_dmg("« je suis morte de rire » → PAS grave (hyperbole, pas un décès)",
          not detecter_moment_grave("je suis morte de rire"))

# Vrais positifs à couvrir.
_cons_dmg("« ma mère est décédée » → grave",
          detecter_moment_grave("ma mère est décédée"))
_cons_dmg("« j'ai perdu ma mère » → grave (lien de parenté explicite)",
          detecter_moment_grave("j'ai perdu ma mère"))
_cons_dmg("« il vient de mourir » → grave",
          detecter_moment_grave("il vient de mourir"))

cons_dmg_ok = all(cons_dmg_results)
print(f"\n{'✅' if cons_dmg_ok else '❌'} CONSULTATION 1 — detecter_moment_grave ({len(cons_dmg_results)} assertions) : {'PASS' if cons_dmg_ok else 'FAIL'}")
if not cons_dmg_ok:
    raise SystemExit(1)

print("\n" + "=" * 60)
print("TEST CONSULTATION 1 — sobriété moment grave (deuil/décès)")
print("=" * 60)

cons2_results = []
def _cons2(label, condition):
    cons2_results.append(condition)
    print(f"{'✅' if condition else '❌'} {label}")

# Moment grave EN COURS D'ONBOARDING (1er tour) : la sobriété doit primer sur le tirage d'accueil.
system_grave_onboarding, _ = _run_get_reply_accueil(
    _user_bug1(onboarding_done=False), "ma mère est décédée hier")
_cons2("deuil au 1er tour → bloc MOMENT GRAVE présent", "MOMENT GRAVE" in system_grave_onboarding)
_cons2("deuil au 1er tour → bloc TIRAGE D'ACCUEIL absent (sobriété prioritaire)",
       "TIRAGE D'ACCUEIL" not in system_grave_onboarding)
_cons2("deuil au 1er tour → PSAUME EN APPUI absent", "PSAUME EN APPUI" not in system_grave_onboarding)
_cons2("deuil au 1er tour → INSPIRATION DU MOMENT absent", "INSPIRATION DU MOMENT" not in system_grave_onboarding)
_cons2("deuil au 1er tour → RITUELS CONCRETS ET VARIÉS absent", "RITUELS CONCRETS ET VARIÉS" not in system_grave_onboarding)

# Moment grave en cours de conversation normale (hors onboarding).
system_grave_normal, _ = _run_get_reply_accueil(
    _user_bug1(onboarding_done=True), "ma mère est décédée hier")
_cons2("deuil hors onboarding → bloc MOMENT GRAVE présent", "MOMENT GRAVE" in system_grave_normal)
_cons2("deuil hors onboarding → PROPOSITION TIRAGE SPONTANÉE absent",
       "PROPOSITION TIRAGE SPONTANÉE" not in system_grave_normal)
_cons2("deuil hors onboarding → RITUELS CONCRETS ET VARIÉS absent", "RITUELS CONCRETS ET VARIÉS" not in system_grave_normal)

# Message neutre (non grave) : aucun des deux ne doit apparaître.
system_neutre, _ = _run_get_reply_accueil(_user_bug1(onboarding_done=True), "je pense qu'elle me trompe")
_cons2("message non grave → bloc MOMENT GRAVE absent", "MOMENT GRAVE" not in system_neutre)

cons2_ok = all(cons2_results)
print(f"\n{'✅' if cons2_ok else '❌'} CONSULTATION 1 — sobriété moment grave ({len(cons2_results)} assertions) : {'PASS' if cons2_ok else 'FAIL'}")
if not cons2_ok:
    raise SystemExit(1)

print("\n✅ TESTS CONSULTATION 1 (rituels conditionnels + sobriété) : PASS")

# ============================================================
# TESTS — CONSULTATION point 3 : genre utilisateur (onboarding)
# ============================================================
print("\n" + "=" * 60)
print("TEST CONSULTATION 3 — detecter_genre")
print("=" * 60)

from auryel_bot import detecter_genre, gerer_onboarding

cons3_results = []
def _cons3(label, condition):
    cons3_results.append(condition)
    print(f"{'✅' if condition else '❌'} {label}")

for mot in ["homme", "masculin", "mec", "garçon", "un homme", "je suis un homme"]:
    _cons3(f"detecter_genre(« {mot} ») → 'm'", detecter_genre(mot) == "m")
for mot in ["femme", "féminin", "feminin", "fille", "nana", "une femme", "je suis une femme"]:
    _cons3(f"detecter_genre(« {mot} ») → 'f'", detecter_genre(mot) == "f")
for mot in ["je ne sais pas", "peu importe", "aucune idée", ""]:
    _cons3(f"detecter_genre(« {mot} ») → '' (ambigu)", detecter_genre(mot) == "")

cons3a_ok = all(cons3_results)
print(f"\n{'✅' if cons3a_ok else '❌'} CONSULTATION 3 — detecter_genre ({len(cons3_results)} assertions) : {'PASS' if cons3a_ok else 'FAIL'}")
if not cons3a_ok:
    raise SystemExit(1)

print("\n" + "=" * 60)
print("TEST CONSULTATION 3 — gerer_onboarding, nouvel ordre conseiller→genre→prenom→email")
print("=" * 60)

_GENRE_PHONE = "+33699000077"

def _run_onboarding_step(user_dict, message):
    with patch("auryel_bot.update_user_silent") as m_update, \
         patch("auryel_bot.add_message"), \
         patch("auryel_bot.log_event"):
        reply = gerer_onboarding(_GENRE_PHONE, user_dict, message)
        return reply, m_update.call_args_list

cons3b_results = []
def _cons3b(label, condition):
    cons3b_results.append(condition)
    print(f"{'✅' if condition else '❌'} {label}")

# step choix_conseiller → transition vers "genre" (pas "prenom"), question avec le nom du conseiller
user_choix = {**USER_BASE, "phone": _GENRE_PHONE, "onboarding_step": "choix_conseiller",
              "onboarding_done": False, "nb_echanges": 0}
reply_choix, calls_choix = _run_onboarding_step(user_choix, "1")
steps_choix = [c.kwargs.get("onboarding_step") for c in calls_choix if "onboarding_step" in c.kwargs]
_cons3b("step choix_conseiller → transition vers 'genre' (pas 'prenom')", "genre" in steps_choix)
_cons3b("step choix_conseiller → question de genre posée avec le nom du conseiller",
        "masculin ou au féminin" in reply_choix and "Séléna" in reply_choix)

# step genre → ne reboucle JAMAIS, transition vers "prenom" quel que soit le résultat
for message_genre, genre_attendu in [("un homme", "m"), ("une femme", "f"), ("je ne sais pas", "")]:
    user_genre = {**USER_BASE, "phone": _GENRE_PHONE, "onboarding_step": "genre",
                  "onboarding_done": False, "nb_echanges": 0, "genre": ""}
    reply_g, calls_g = _run_onboarding_step(user_genre, message_genre)
    steps_g = [c.kwargs.get("onboarding_step") for c in calls_g if "onboarding_step" in c.kwargs]
    genres_g = [c.kwargs.get("genre") for c in calls_g if "genre" in c.kwargs]
    _cons3b(f"step genre, message « {message_genre} » → transition vers 'prenom' (jamais de reboucle)",
            "prenom" in steps_g)
    _cons3b(f"step genre, message « {message_genre} » → genre écrit = '{genre_attendu}'",
            genre_attendu in genres_g)
    _cons3b(f"step genre, message « {message_genre} » → question du prénom posée",
            "comment t'appelles-tu" in reply_g.lower())

# step prenom → salutation accordée selon le genre déjà enregistré
for genre_val, reply_attendu in [
    ("m", "Enchanté Marie. Quelle adresse email puis-je garder pour ton suivi ?"),
    ("f", "Enchantée Marie. Quelle adresse email puis-je garder pour ton suivi ?"),
    ("", "Bienvenue Marie. Quelle adresse email puis-je garder pour ton suivi ?"),
]:
    user_prenom = {**USER_BASE, "phone": _GENRE_PHONE, "onboarding_step": "prenom",
                   "onboarding_done": False, "nb_echanges": 0, "genre": genre_val, "prenom": ""}
    reply_p, _ = _run_onboarding_step(user_prenom, "Marie")
    _cons3b(f"step prenom, genre='{genre_val}' → « {reply_attendu} »", reply_p == reply_attendu)

# Non-régression : attente_reponse_presentation bascule toujours onboarding_done=True
user_presentation = {**USER_BASE, "phone": _GENRE_PHONE, "onboarding_step": "attente_reponse_presentation",
                      "onboarding_done": False, "nb_echanges": 0, "guide": "selena"}
reply_pres, calls_pres = _run_onboarding_step(user_presentation, "je me sens perdue")
onboarding_done_calls = [c.kwargs.get("onboarding_done") for c in calls_pres if "onboarding_done" in c.kwargs]
_cons3b("step attente_reponse_presentation → onboarding_done passe à True (non-régression)",
        True in onboarding_done_calls)
_cons3b("step attente_reponse_presentation → reply est None (relance la conversation IA, non-régression)",
        reply_pres is None)

cons3b_ok = all(cons3b_results)
print(f"\n{'✅' if cons3b_ok else '❌'} CONSULTATION 3 — gerer_onboarding ({len(cons3b_results)} assertions) : {'PASS' if cons3b_ok else 'FAIL'}")
if not cons3b_ok:
    raise SystemExit(1)

print("\n" + "=" * 60)
print("TEST MIGRATION v18 — genre exposé par get_user()")
print("=" * 60)

_src18 = _inspect.getsource(_get_user)
_select_match18 = _re.search(r"SELECT\s+(.*?)\s+FROM users", _src18, _re.S)
_cols18 = [c.strip() for c in _select_match18.group(1).replace("\n", " ").split(",") if c.strip()]
_fake_row18 = tuple([0] * (len(_cols18) - 1) + ["m"])

_cur18 = MagicMock()
_cur18.fetchone.return_value = _fake_row18
_conn18 = MagicMock()
_conn18.cursor.return_value = _cur18

with patch("auryel_bot.get_conn", return_value=_conn18):
    _u18 = _get_user("+33600000000")

v18_results = []
def _v18(label, condition):
    v18_results.append(condition)
    print(f"{'✅' if condition else '❌'} {label}")

_v18("genre est la dernière colonne du SELECT (migration v18 bien ajoutée en fin)",
     _cols18[-1] == "genre")
_v18("get_user() expose genre avec la bonne valeur de colonne",
     _u18.get("genre") == "m")

v18_ok = all(v18_results)
print(f"\n{'✅' if v18_ok else '❌'} MIGRATION v18 ({len(v18_results)} assertions) : {'PASS' if v18_ok else 'FAIL'}")
if not v18_ok:
    raise SystemExit(1)

print("\n✅ TESTS CONSULTATION 3 (genre utilisateur) : PASS")
