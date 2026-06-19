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
