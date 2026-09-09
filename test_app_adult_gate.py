"""
test_app_adult_gate.py — Contrôle 18+ AUTORITÉ SERVEUR (Partie B).

Le cœur du contrôle est _adult_gate_check() : source = app_profiles.date_naissance
UNIQUEMENT, horloge = serveur, codes d'erreur STABLES. Testé en unité avec une
date « aujourd'hui » figée + get_or_create_app_profile monkeypatché. Deux tests
d'intégration vérifient que /api/consultation/message et /api/consultation/open
appliquent bien le gate AVANT toute mutation, et qu'une date/âge envoyés dans le
body n'ont AUCUN effet.

100 % local : psycopg2 mocké, aucun LLM, aucun réseau.
"""

import sys
import inspect
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

import os
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


UID1 = "11111111-1111-4111-8111-111111111111"

# Profil piloté par les tests : seul date_naissance compte pour le gate.
_PROFILE = {"guide": "selena", "date_naissance": ""}
A.get_or_create_app_profile = lambda uid: dict(_PROFILE)
A.get_app_profile = lambda uid: dict(_PROFILE)


def _set_dob(value):
    _PROFILE["date_naissance"] = value


# ===========================================================================
# 1. _adult_gate_check — UNITÉ (horloge figée via le paramètre `today`)
# ===========================================================================
TODAY = date(2026, 9, 9)

_set_dob("")
g = A._adult_gate_check(UID1, today=TODAY)
check(g is not None and g[1] == 403 and g[0] == {"error": "age_verification_required"},
      "1a date de naissance absente -> 403 age_verification_required")

_set_dob("   ")
g = A._adult_gate_check(UID1, today=TODAY)
check(g is not None and g[0]["error"] == "age_verification_required",
      "1b date blanche -> age_verification_required")

_set_dob("pas-une-date")
g = A._adult_gate_check(UID1, today=TODAY)
check(g is not None and g[0]["error"] == "age_verification_required",
      "1c date illisible -> age_verification_required")

# 17 ans : anniversaire des 18 ans dans le futur proche
_set_dob("2009-01-01")
g = A._adult_gate_check(UID1, today=TODAY)
check(g is not None and g[1] == 403 and g[0] == {"error": "adult_required"},
      "1d 17 ans -> 403 adult_required")

# atteint 18 ans EXACTEMENT aujourd'hui -> ADMIS
_set_dob("2008-09-09")
check(A._adult_gate_check(UID1, today=TODAY) is None,
      "1e 18 ans pile aujourd'hui -> admis (None)")

# 18 ans depuis hier -> admis
_set_dob("2008-09-08")
check(A._adult_gate_check(UID1, today=TODAY) is None, "1f 18 ans + 1 jour -> admis")

# anniversaire des 18 ans DEMAIN -> refusé
_set_dob("2008-09-10")
g = A._adult_gate_check(UID1, today=TODAY)
check(g is not None and g[0]["error"] == "adult_required",
      "1g 18 ans demain -> adult_required")

# adulte large
_set_dob("1990-06-15")
check(A._adult_gate_check(UID1, today=TODAY) is None, "1h adulte -> admis")

# date future -> âge négatif -> adult_required (jamais admis)
_set_dob("2030-01-01")
g = A._adult_gate_check(UID1, today=TODAY)
check(g is not None and g[0]["error"] == "adult_required",
      "1i date future -> adult_required, jamais admis")

# ===========================================================================
# 2. Horloge SERVEUR : sans `today`, _adult_gate_check utilise date.today()
# ===========================================================================
_set_dob(date.today().replace(year=date.today().year - 30).isoformat())
check(A._adult_gate_check(UID1) is None,
      "2a sans paramètre today : horloge serveur, adulte -> admis")
_set_dob(date.today().replace(year=date.today().year - 5).isoformat())
g = A._adult_gate_check(UID1)
check(g is not None and g[0]["error"] == "adult_required",
      "2b sans paramètre today : mineur -> adult_required")

# ===========================================================================
# 3. Les routes consultation appliquent le gate (source)
# ===========================================================================
for fn in (A.api_consultation_message, A.api_consultation_open):
    src = inspect.getsource(fn)
    check("_adult_gate_check(" in src, f"3 {fn.__name__} appelle _adult_gate_check")

# ===========================================================================
# 4. Intégration : 403 stable AVANT toute mutation + body sans effet
# ===========================================================================
A.app.config["TESTING"] = True
try:
    A.limiter.enabled = False
except Exception:
    pass
client = A.app.test_client()
A.resolve_app_session = lambda tok: (
    {"session_id": "s", "user_id": UID1, "email": "s@auryel.co"} if tok else None
)

# get_conn ne doit JAMAIS être atteint pour un mineur : on le piège.
_SENTINEL = {"hit": False}


class _Boom:
    def cursor(self):
        _SENTINEL["hit"] = True
        raise AssertionError("get_conn atteint malgré le gate 18+")

    def close(self):
        pass


A.get_conn = lambda: _Boom()
H = {"Authorization": "Bearer x"}

# 4a — mineur : /api/consultation/message -> 403 adult_required, aucune mutation
_set_dob("2012-01-01")
_SENTINEL["hit"] = False
r = client.post("/api/consultation/message", json={"message": "bonjour"}, headers=H)
check(r.status_code == 403 and r.get_json() == {"error": "adult_required"},
      "4a message, mineur -> 403 adult_required")
check(_SENTINEL["hit"] is False, "4b aucune I/O DB déclenchée pour un mineur")

# 4c — date absente : /api/consultation/open -> 403 age_verification_required
_set_dob("")
r = client.post("/api/consultation/open", json={"advisor_id": "selena"}, headers=H)
check(r.status_code == 403 and r.get_json() == {"error": "age_verification_required"},
      "4c open, date absente -> 403 age_verification_required")

# 4d — body qui tente de fournir date_naissance / age : AUCUN effet (profil mineur)
_set_dob("2012-01-01")
r = client.post("/api/consultation/message",
                json={"message": "bonjour", "date_naissance": "1990-01-01", "age": 42},
                headers=H)
check(r.status_code == 403 and r.get_json() == {"error": "adult_required"},
      "4d date_naissance/age dans le body -> ignorés, toujours 403")

# 4e — sans Bearer -> 401 (auth avant gate)
r = client.post("/api/consultation/message", json={"message": "x"}, headers={})
check(r.status_code == 401, "4e sans Bearer -> 401")

# ---------------------------------------------------------------------------
print("-" * 60)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
