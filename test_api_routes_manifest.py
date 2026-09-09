"""
test_api_routes_manifest.py — le manifeste docs/api_routes_manifest.json ne
DÉRIVE PAS des routes réellement enregistrées par auryel_bot.py.

Ce manifeste est le contrat que le test Flutter vérifie côté app : chaque
chemin API critique appelé par l'application doit y figurer. Ici on garantit
juste qu'il reste synchrone avec le backend :
  - chaque (method, path) du manifeste EXISTE dans app.url_map ;
  - toute route /api/app/* ou /api/consultation/* réellement exposée est
    listée dans le manifeste (sinon un endpoint nouveau passerait sous le
    radar du contrat Flutter).
100 % local : psycopg2 mocké.
"""

import sys
import json
import os
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

for _k, _v in {
    "SECRET_KEY": "test-secret-key", "VERIFY_TOKEN": "test", "ADMIN_PASSWORD": "test",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "STRIPE_SK": "sk_test_placeholder", "STRIPE_WEBHOOK_SECRET": "whsec_placeholder",
    "WHATSAPP_TOKEN": "test", "PHONE_NUMBER_ID": "test", "GROQ_API_KEY": "test",
    "RESEND_API_KEY": "test", "META_APP_SECRET": "test", "DAILY_SECRET": "test",
    "SEO_SECRET": "test",
}.items():
    os.environ.setdefault(_k, _v)

import auryel_bot as A

_STATE = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _STATE["pass"] += 1
        print(f"OK  {label}")
    else:
        _STATE["fail"] += 1
        print(f"XX  {label}")


HERE = os.path.dirname(os.path.abspath(A.__file__))
MANIFEST = json.load(open(os.path.join(HERE, "docs", "api_routes_manifest.json")))

manifest_pairs = {(r["method"], r["path"]) for r in MANIFEST["routes"]}

# routes réelles (méthodes métier uniquement)
real_pairs = set()
for rule in A.app.url_map.iter_rules():
    for m in rule.methods:
        if m in ("GET", "POST", "PATCH", "PUT", "DELETE"):
            real_pairs.add((m, rule.rule))

# 1. tout le manifeste existe réellement
missing = sorted(p for p in manifest_pairs if p not in real_pairs)
check(not missing, f"1 chaque entrée du manifeste existe dans app.url_map "
                   f"(manquantes: {missing})")

# 2. toute route app/consultation exposée est dans le manifeste
CRITICAL_PREFIXES = ("/api/app/", "/api/consultation/", "/api/auth/",
                     "/api/account", "/api/tirages", "/api/billing/", "/health")
real_critical = {
    (m, p) for (m, p) in real_pairs
    if any(p.startswith(pre) for pre in CRITICAL_PREFIXES)
}
uncovered = sorted(p for p in real_critical if p not in manifest_pairs)
check(not uncovered, f"2 aucune route mobile critique absente du manifeste "
                     f"(non couvertes: {uncovered})")

# 3. le manifeste est versionné
check(isinstance(MANIFEST.get("version"), int) and MANIFEST["version"] >= 1,
      "3 manifeste versionné")

# ---------------------------------------------------------------------------
print("-" * 60)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
