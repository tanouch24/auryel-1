"""
test_cron_push_test.py — POST /cron/push-test (envoi de test ciblé, protégé).

Vérifie : auth constant-time (même secret que /cron/push-tick), validation
(email / catégorie), résolution du compte par email, envoi UNIQUEMENT aux
jetons actifs du compte, désactivation d'un jeton rejeté définitivement par
FCM, aucun jeton renvoyé dans la réponse, respect de PUSH dry-run.

AUCUN réseau : FcmSender est remplacé par un faux. DB = fake conn mémoire.
Script (pas pytest) : sys.exit(1) si un test échoue.
"""

import sys
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

import os
for _k, _v in {
    "SECRET_KEY": "t", "VERIFY_TOKEN": "t", "ADMIN_PASSWORD": "t",
    "DATABASE_URL": "postgresql://t:t@localhost/t",
    "STRIPE_SK": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "WHATSAPP_TOKEN": "t", "PHONE_NUMBER_ID": "t", "GROQ_API_KEY": "t",
    "RESEND_API_KEY": "t", "META_APP_SECRET": "t",
    "DAILY_SECRET": "cron-secret-xyz", "SEO_SECRET": "t",
}.items():
    os.environ.setdefault(_k, _v)

import auryel_bot as A

_S = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _S["pass"] += 1
        print(f"OK  {label}")
    else:
        _S["fail"] += 1
        print(f"XX  {label}")


A.app.config["TESTING"] = True
try:
    A.limiter.enabled = False
except Exception:
    pass
client = A.app.test_client()

SECRET = "cron-secret-xyz"
UID = "11111111-1111-4111-8111-111111111111"
EMAIL = "Test.User@Example.com"
EMAIL_NORM = "test.user@example.com"

# --- fake DB : accounts (lookup par email) --------------------------------
DB = {"account": {"email_normalized": EMAIL_NORM, "user_id": UID, "deleted": False},
      "invalidated": []}


class _Cur:
    def __init__(self):
        self._r = None

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self._r = None
        if "FROM accounts WHERE email_normalized=%s AND deleted_at IS NULL" in s:
            acc = DB["account"]
            if acc and acc["email_normalized"] == params[0] and not acc["deleted"]:
                self._r = (acc["user_id"],)
        elif "UPDATE push_devices SET enabled=FALSE, invalid_at" in s:
            DB["invalidated"].append(params[-1] if params else None)

    def fetchone(self):
        return self._r

    def close(self):
        pass


class _Conn:
    def cursor(self):
        return _Cur()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


A.get_conn = lambda: _Conn()

# --- jetons actifs du compte (stub) --------------------------------------
TOKENS = {"active": ["tok-A", "tok-B"]}
A.active_push_tokens_for_user = lambda uid: list(TOKENS["active"]) if uid == UID else []
A.mark_push_token_invalid = lambda tok, now=None: DB["invalidated"].append(tok)
A._user_hash = lambda uid: "hash"
A.log_event = lambda *a, **k: None

# --- faux FcmSender injecté dans push_fcm --------------------------------
import push_fcm as PF


class _FakeRes:
    def __init__(self, outcome):
        self.outcome = outcome
        self.error = None


class _FakeSender:
    mode = "sent"          # sent | dry_run | invalid_token | transient_error

    def __init__(self, config, **kw):
        self.config = config
        self.calls = []

    def send(self, token, category, title, body, **kw):
        self.calls.append((token, category, title, body))
        _FakeSender.last = self
        m = _FakeSender.mode
        if m == "per_token":
            return _FakeRes("invalid_token" if token == "tok-B" else "sent")
        return _FakeRes(m)


PF.FcmSender = _FakeSender


def post(payload, hdr=None):
    return client.post("/cron/push-test", json=payload, headers=hdr or {})


print("=" * 60)
# 0 route enregistrée
_rules = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
check(("/cron/push-test", "POST") in _rules, "0 route POST /cron/push-test")

# 1 pas de secret -> 401
r = post({"email": EMAIL, "category": "daily_thought"})
check(r.status_code == 401, "1 sans secret -> 401")

# 2 mauvais secret -> 401
r = post({"secret": "nope", "email": EMAIL, "category": "daily_thought"})
check(r.status_code == 401, "2 mauvais secret -> 401")

# 3 secret via header X-Cron-Secret accepté
_FakeSender.mode = "sent"
r = post({"email": EMAIL, "category": "daily_thought"},
         hdr={"X-Cron-Secret": SECRET})
check(r.status_code == 200, "3 secret via header -> 200")

# 4 email invalide -> 400
r = post({"secret": SECRET, "email": "pasunemail", "category": "daily_thought"})
check(r.status_code == 400 and r.get_json()["error"] == "invalid_email",
      "4 email invalide -> 400 invalid_email")

# 5 catégorie hors allowlist -> 400
r = post({"secret": SECRET, "email": EMAIL, "category": "spam"})
check(r.status_code == 400 and r.get_json()["error"] == "invalid_category",
      "5 catégorie non autorisée -> 400 invalid_category")

# 6 compte inconnu -> 404
r = post({"secret": SECRET, "email": "ghost@example.com",
          "category": "daily_thought"})
check(r.status_code == 404 and r.get_json()["error"] == "account_not_found",
      "6 compte inconnu -> 404")

# 7 envoi OK : 2 jetons -> sent=2, réponse SANS aucun jeton
_FakeSender.mode = "sent"
r = post({"secret": SECRET, "email": EMAIL, "category": "daily_meditation"})
j = r.get_json()
check(r.status_code == 200 and j["targeted_devices"] == 2 and j["sent"] == 2
      and j["invalidated"] == 0 and j["failed"] == 0,
      "7 2 jetons actifs -> sent=2")
check("tok-A" not in r.get_data(as_text=True)
      and "tok-B" not in r.get_data(as_text=True),
      "7b aucun jeton FCM dans la réponse")
check(_FakeSender.last.calls[0][1] == "daily_meditation",
      "7c catégorie transmise au sender")

# 8 jeton rejeté définitivement -> désactivé + invalidated compté
DB["invalidated"].clear()
_FakeSender.mode = "per_token"
r = post({"secret": SECRET, "email": EMAIL, "category": "personal_guidance"})
j = r.get_json()
check(r.status_code == 200 and j["sent"] == 1 and j["invalidated"] == 1
      and "tok-B" in DB["invalidated"],
      "8 jeton FCM invalide -> mark_push_token_invalid('tok-B')")

# 9 aucun appareil -> status no_device, 200
TOKENS["active"] = []
r = post({"secret": SECRET, "email": EMAIL, "category": "daily_thought"})
j = r.get_json()
check(r.status_code == 200 and j["status"] == "no_device"
      and j["targeted_devices"] == 0,
      "9 aucun jeton actif -> no_device")
TOKENS["active"] = ["tok-A", "tok-B"]

# 10 dry-run : sender renvoie dry_run -> sent compté, outcomes=['dry_run']
_FakeSender.mode = "dry_run"
r = post({"secret": SECRET, "email": EMAIL, "category": "daily_thought"})
j = r.get_json()
check(r.status_code == 200 and j["sent"] == 2 and j["outcomes"] == ["dry_run", "dry_run"],
      "10 dry-run -> outcomes=['dry_run', ...]")

# 11 config_error propagé sans exception
_FakeSender.mode = "config_error"
r = post({"secret": SECRET, "email": EMAIL, "category": "daily_thought"})
j = r.get_json()
check(r.status_code == 200 and j["failed"] == 2 and j["sent"] == 0,
      "11 config_error -> failed compté, pas de crash")

# 12 titre/corps personnalisés bornés (<=120 / <=240) et transmis
_FakeSender.mode = "sent"
r = post({"secret": SECRET, "email": EMAIL, "category": "daily_thought",
          "title": "T" * 200, "body": "B" * 400})
tok, cat, title, body = _FakeSender.last.calls[-1]
check(len(title) == 120 and len(body) == 240,
      "12 titre/corps bornés à 120/240")

print("-" * 60)
print(f"RÉSULTAT : {_S['pass']} ok / {_S['fail']} ko")
sys.exit(1 if _S["fail"] else 0)
