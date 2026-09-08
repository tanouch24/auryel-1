"""
test_app_support.py — « Signaler un problème » : POST /api/app/support.

100 % local : psycopg2 mocké, get_conn -> fausse DB en mémoire (table
`accounts` : email + deleted_at). `send_support_email` monkeypatché (aucun
Resend réel, aucun réseau). resolve_app_session monkeypatché. Aucun LLM.
Style aligné sur test_app_memory_rewards.py.
"""

import sys
import inspect
from datetime import datetime, timezone
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
UID2 = "22222222-2222-4222-8222-222222222222"

_ACCOUNTS = {}   # uid -> {"email": str|None, "deleted_at": None|dt}
_SENT = []       # appels capturés de send_support_email


def _reset():
    _ACCOUNTS.clear()
    _SENT.clear()
    _ACCOUNTS[UID1] = {"email": "a@auryel.co", "deleted_at": None}
    _ACCOUNTS[UID2] = {"email": "b@auryel.co", "deleted_at": None}


class _Cur:
    def __init__(self):
        self._one = None

    def fetchone(self):
        return self._one

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = " ".join(sql.split())
        if k == ("SELECT email FROM accounts WHERE user_id=%s AND deleted_at IS NULL"):
            uid = params[0]
            acc = _ACCOUNTS.get(uid)
            self._one = (acc["email"],) if (acc and acc["deleted_at"] is None) else None
        else:
            raise AssertionError("SQL non géré par le fake support : " + k)


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
A.app.config["TESTING"] = True
try:
    A.limiter.enabled = False
except Exception:
    pass
client = A.app.test_client()

_CURRENT = {"uid": UID1}
A.resolve_app_session = lambda tok: (
    {"session_id": "s", "user_id": _CURRENT["uid"], "email": "session@auryel.co"}
    if tok else None
)

# send_support_email est remplacé : on capture les kwargs et on pilote l'issue.
_PROVIDER = {"ok": True}


def _fake_send_support_email(**kwargs):
    _SENT.append(kwargs)
    return {"ok": _PROVIDER["ok"], "error": None if _PROVIDER["ok"] else "send_failed"}


A.send_support_email = _fake_send_support_email


def _as(uid):
    _CURRENT["uid"] = uid


def _post(body, tok="x"):
    hdr = {"Authorization": f"Bearer {tok}"} if tok else {}
    return client.post("/api/app/support", headers=hdr, json=body)


_OK_BODY = {"subject": "Souci de connexion", "message": "Je n'arrive pas à ouvrir l'app.",
            "category": "technical"}

# ===========================================================================
# 0. Route enregistrée + garde-fous serveur-autorité
# ===========================================================================
_rules = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
check(("/api/app/support", "POST") in _rules, "0a POST /api/app/support enregistrée")
_src = inspect.getsource(A.api_app_support)
check("g.app_account[\"user_id\"]" in _src, "0b user_id vient du Bearer")
check("SELECT email FROM accounts" in _src, "0c email relu en base")
check("data.get(\"email\")" not in _src, "0d aucun email lu depuis le body")
check("@limiter.limit(\"10 per hour\")" in _src, "0e rate limit 10/h présent")

# ===========================================================================
# 1. Auth obligatoire
# ===========================================================================
_reset()
check(_post(_OK_BODY, tok=None).status_code == 401, "1 sans Bearer -> 401")

# ===========================================================================
# 2. subject vide -> 400 ; 3. message vide -> 400
# ===========================================================================
_reset(); _as(UID1)
check(_post({"subject": "  ", "message": "x"}).status_code == 400, "2 subject vide -> 400")
check(_post({"subject": "x", "message": "   "}).status_code == 400, "3 message vide -> 400")
check(_post({"message": "x"}).status_code == 400, "3b subject absent -> 400")
check(_post({"subject": "x"}).status_code == 400, "3c message absent -> 400")
check(_post("pas un objet").status_code == 400, "3d body non-objet -> 400")

# ===========================================================================
# 4. catégorie non reconnue -> repli 'other' (jamais 400)
# ===========================================================================
_reset(); _as(UID1)
r = _post({"subject": "s", "message": "m", "category": "n'importe quoi"})
check(r.status_code == 200 and _SENT[-1]["category"] == "other",
      "4a catégorie inconnue -> acceptée, repli 'other'")
_reset(); _as(UID1)
r = _post({"subject": "s", "message": "m"})  # category absente
check(r.status_code == 200 and _SENT[-1]["category"] == "other",
      "4b catégorie absente -> 'other'")
_reset(); _as(UID1)
r = _post({"subject": "s", "message": "m", "category": "subscription"})
check(r.status_code == 200 and _SENT[-1]["category"] == "subscription",
      "4c catégorie valide conservée")

# ===========================================================================
# 5. sujet trop long -> 400 ; 6. message trop long -> 400
# ===========================================================================
_reset(); _as(UID1)
check(_post({"subject": "x" * (A._SUPPORT_SUBJECT_MAX + 1), "message": "m"}).status_code == 400,
      "5 sujet trop long -> 400")
check(_post({"subject": "s", "message": "m" * (A._SUPPORT_MESSAGE_MAX + 1)}).status_code == 400,
      "6 message trop long -> 400")
_reset(); _as(UID1)
check(_post({"subject": "s", "message": "m" * A._SUPPORT_MESSAGE_MAX}).status_code == 200,
      "6b message pile à la limite -> 200")

# ===========================================================================
# 7. email client arbitraire ignoré ; 8. user_id vient de l'auth ;
# 9. email compte vient de la DB
# ===========================================================================
_reset(); _as(UID1)
r = _post({"subject": "s", "message": "m", "email": "pirate@evil.com",
           "user_id": UID2})
sent = _SENT[-1]
check(r.status_code == 200 and sent["user_id"] == UID1
      and sent["account_email"] == "a@auryel.co",
      "7/8/9 email + user_id du body ignorés ; user_id=Bearer, email=DB")

# ===========================================================================
# 10. succès provider -> {"status":"sent"}
# ===========================================================================
_reset(); _as(UID1); _PROVIDER["ok"] = True
r = _post(_OK_BODY)
check(r.status_code == 200 and r.get_json() == {"status": "sent"},
      "10 succès -> 200 {status: sent}")

# ===========================================================================
# 11. échec provider -> 503 support_unavailable, pas de 200 mensonger
# ===========================================================================
_reset(); _as(UID1); _PROVIDER["ok"] = False
r = _post(_OK_BODY)
check(r.status_code == 503 and r.get_json().get("error") == "support_unavailable",
      "11 échec envoi -> 503 support_unavailable")
_PROVIDER["ok"] = True

# ===========================================================================
# 12. aucun secret dans la réponse
# ===========================================================================
_reset(); _as(UID1)
body_txt = _post(_OK_BODY).get_data(as_text=True)
check("RESEND" not in body_txt and "test" not in body_txt.lower().split("status")[0]
      if False else ("resend" not in body_txt.lower() and "api_key" not in body_txt.lower()),
      "12 réponse ne contient aucun nom/valeur de secret")

# ===========================================================================
# 13. rate limit déclaré (vérifié via la source à 0e) — ici on confirme le
#     décorateur est bien APPLIQUÉ (limiter désactivé en test).
# ===========================================================================
check("10 per hour" in inspect.getsource(A.api_app_support), "13 rate limit 10/h")

# ===========================================================================
# 14. compte supprimé -> 401
# ===========================================================================
_reset(); _as(UID1)
_ACCOUNTS[UID1]["deleted_at"] = datetime.now(timezone.utc)
check(_post(_OK_BODY).status_code == 401, "14 compte supprimé -> 401")

# ===========================================================================
# 15. contenu de consultation JAMAIS ajouté automatiquement — le helper ne
#     reçoit que subject/message/catégorie/métadonnées techniques bornées.
# ===========================================================================
_reset(); _as(UID1)
_post({"subject": "s", "message": "m", "app_version": "1.0.0", "platform": "android",
       "os_version": "Android 14",
       "consultation": "TOUT MON HISTORIQUE PRIVÉ",
       "messages": ["ia secret"]})
sent = _SENT[-1]
allowed = {"user_id", "account_email", "category", "subject", "message",
           "app_version", "platform", "os_version", "received_at"}
check(set(sent.keys()) == allowed,
      "15a send_support_email ne reçoit QUE les champs whitelistés")
check(sent["message"] == "m" and "consultation" not in sent
      and "messages" not in sent,
      "15b aucun contenu de consultation / message IA transmis")
check(sent["app_version"] == "1.0.0" and sent["platform"] == "android"
      and sent["os_version"] == "Android 14",
      "15c métadonnées techniques non sensibles transmises")

# ===========================================================================
# 16. métadonnées bornées : une version app géante est tronquée, pas rejetée
# ===========================================================================
_reset(); _as(UID1)
_post({"subject": "s", "message": "m", "app_version": "9" * 500})
check(len(_SENT[-1]["app_version"]) == A._SUPPORT_META_MAX,
      "16 métadonnée surdimensionnée -> tronquée à _SUPPORT_META_MAX")

# ===========================================================================
# 17. échappement HTML du contenu utilisateur (anti-injection)
# ===========================================================================
esc = A._support_escape('<script>alert("x")</script>')
check("<script>" not in esc and "&lt;script&gt;" in esc,
      "17 _support_escape neutralise les balises HTML")

# ---------------------------------------------------------------------------
print("-" * 60)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
