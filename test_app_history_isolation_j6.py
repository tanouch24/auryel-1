"""
test_app_history_isolation_j6.py — J6-B1 : CLOISONNEMENT de l'historique IA.

Bug corrigé : `get_history_for_user_id(user_id, limit=20)` renvoyait les 20
derniers messages du compte, TOUTES consultations / TOUS conseillers confondus,
et ce fil était injecté tel quel dans le prompt LLM (get_reply_for_user_id ->
_reply_core). Deux conversations avec des conseillers différents se
contaminaient donc mutuellement (confidentialité conversationnelle).

Correctif : `get_history_for_user_id(user_id, limit=20, consultation_id=...)`.
Quand `consultation_id` est fourni, le SELECT est borné
`WHERE user_id=%s AND consultation_id=%s`. Le chemin mobile
(get_reply_for_user_id -> _io["get_history"]) passe désormais le
`consultation_id` du fil ciblé ; l'endpoint POST /api/consultation/message
passe `consultation_id=cid` à get_reply_for_user_id.

Scénario métier :
    Séléna (fil cid_selena) parle de « SECRET SELENA »
    Ezra   (fil cid_ezra)   parle de « SECRET EZRA »
    -> l'historique servi pour Ezra contient SECRET EZRA, jamais SECRET SELENA
    -> l'historique servi pour Séléna contient SECRET SELENA, jamais SECRET EZRA

100 % local : get_conn -> fausse DB mémoire minimale (INSERT messages + les 2
formes de SELECT que get_history_for_user_id émet). Aucun réseau, aucun LLM,
aucune vraie DB.
"""

import inspect
import sys
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

import os
for _k, _v in {
    "SECRET_KEY": "test-secret-key", "VERIFY_TOKEN": "test", "ADMIN_PASSWORD": "test",
    "CRON_SECRET": "test", "DATABASE_URL": "postgresql://test:test@localhost/test",
    "STRIPE_SK": "sk_test_placeholder", "STRIPE_WEBHOOK_SECRET": "whsec_placeholder",
    "WHATSAPP_TOKEN": "test", "PHONE_NUMBER_ID": "test", "GROQ_API_KEY": "test",
    "RESEND_API_KEY": "test", "META_APP_SECRET": "test", "DAILY_SECRET": "test",
    "SEO_SECRET": "test", "TAROT_MEDIA_UPLOAD_DISABLED": "1",
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


# ---------------------------------------------------------------------------
# Fausse DB mémoire minimale : ce qu'il faut pour add_message_for_user_id +
# get_history_for_user_id (les deux formes de SELECT).
# ---------------------------------------------------------------------------
_MSGS = []
_SEQ = [0]


class _Cur:
    def __init__(self):
        self._rows = None

    def execute(self, sql, params=()):
        k = " ".join(sql.split())
        p = params
        self._rows = None
        if k == ("INSERT INTO messages (user_id, phone, role, content, timestamp, "
                 "consultation_id) VALUES (%s, NULL, %s, %s, %s, %s)"):
            uid, role, content, ts, cid = p
            _SEQ[0] += 1
            _MSGS.append({"id": _SEQ[0], "user_id": str(uid), "role": role,
                          "content": content, "consultation_id": cid})
        elif k == ("SELECT role,content FROM messages WHERE user_id=%s "
                   "AND consultation_id=%s ORDER BY id DESC LIMIT %s"):
            uid, cid, limit = p
            rows = sorted(
                [m for m in _MSGS
                 if m["user_id"] == str(uid)
                 and str(m["consultation_id"]) == str(cid)],
                key=lambda m: m["id"], reverse=True)
            self._rows = [(m["role"], m["content"]) for m in rows[:limit]]
        elif k == ("SELECT role,content FROM messages WHERE user_id=%s "
                   "ORDER BY id DESC LIMIT %s"):
            uid, limit = p
            rows = sorted([m for m in _MSGS if m["user_id"] == str(uid)],
                          key=lambda m: m["id"], reverse=True)
            self._rows = [(m["role"], m["content"]) for m in rows[:limit]]
        else:
            raise AssertionError(f"SQL non géré par le fake J6 : {k}")

    def fetchall(self):
        return self._rows or []

    def fetchone(self):
        return None

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


UID = "11111111-1111-1111-1111-111111111111"
CID_SELENA = "aaaaaaaa-0000-0000-0000-000000000001"
CID_EZRA = "bbbbbbbb-0000-0000-0000-000000000002"

_MSGS.clear()
_SEQ[0] = 0

# Fil Séléna
A.add_message_for_user_id(UID, "user", "Séléna, je te confie SECRET SELENA.",
                          consultation_id=CID_SELENA)
A.add_message_for_user_id(UID, "assistant", "Séléna entend ton SECRET SELENA.",
                          consultation_id=CID_SELENA)
# Fil Ezra
A.add_message_for_user_id(UID, "user", "Ezra, voici SECRET EZRA.",
                          consultation_id=CID_EZRA)
A.add_message_for_user_id(UID, "assistant", "Ezra retient SECRET EZRA.",
                          consultation_id=CID_EZRA)

print("-" * 64)
print("J6-B1 — cloisonnement des données d'historique par consultation_id")
print("-" * 64)

# 1/2 — historique ciblé Ezra
h_ezra = A.get_history_for_user_id(UID, limit=20, consultation_id=CID_EZRA)
blob_ezra = " ".join(m["content"] for m in h_ezra)
check("SECRET EZRA" in blob_ezra,
      "1 historique du fil Ezra CONTIENT SECRET EZRA")
check("SECRET SELENA" not in blob_ezra,
      "2 historique du fil Ezra NE CONTIENT PAS SECRET SELENA")

# 3/4 — historique ciblé Séléna
h_selena = A.get_history_for_user_id(UID, limit=20, consultation_id=CID_SELENA)
blob_selena = " ".join(m["content"] for m in h_selena)
check("SECRET SELENA" in blob_selena,
      "3 historique du fil Séléna CONTIENT SECRET SELENA")
check("SECRET EZRA" not in blob_selena,
      "4 historique du fil Séléna NE CONTIENT PAS SECRET EZRA")

# 5 — ordre chronologique conservé (ancien -> récent)
check([m["role"] for m in h_ezra] == ["user", "assistant"],
      "5 ordre chronologique conservé sur le fil ciblé")

# 6 — legacy : consultation_id=None -> tout le compte (compat WhatsApp/Telegram)
h_all = A.get_history_for_user_id(UID, limit=20)
blob_all = " ".join(m["content"] for m in h_all)
check("SECRET EZRA" in blob_all and "SECRET SELENA" in blob_all and len(h_all) == 4,
      "6 legacy (sans consultation_id) : historique complet du compte inchangé")

# 7 — un fil inexistant -> historique vide (jamais de repli sur tout le compte)
h_none = A.get_history_for_user_id(UID, limit=20,
                                   consultation_id="cccccccc-0000-0000-0000-000000000009")
check(h_none == [],
      "7 fil inconnu -> historique vide (aucun repli silencieux sur le compte)")

# ---------------------------------------------------------------------------
# Câblage : le chemin MOBILE passe bien le consultation_id à get_history.
# ---------------------------------------------------------------------------
print("-" * 64)
print("J6-B1 — câblage du cloisonnement dans le chemin mobile")
print("-" * 64)

_src_reply = inspect.getsource(A.get_reply_for_user_id)
check('get_history_for_user_id(' in _src_reply
      and 'consultation_id=consultation_id' in _src_reply,
      "8 get_reply_for_user_id : _io['get_history'] transmet consultation_id")

_src_msg = inspect.getsource(A.api_consultation_message)
check('get_reply_for_user_id(' in _src_msg and 'consultation_id=cid' in _src_msg,
      "9 api_consultation_message : passe consultation_id=cid à get_reply_for_user_id")

# get_history_for_user_id : signature rétro-compatible (param optionnel).
_sig = inspect.signature(A.get_history_for_user_id)
check(list(_sig.parameters) == ["user_id", "limit", "consultation_id"]
      and _sig.parameters["consultation_id"].default is None,
      "10 get_history_for_user_id(user_id, limit=20, consultation_id=None) : "
      "param optionnel, defaut None")

# ---------------------------------------------------------------------------
print()
_total = _STATE["pass"] + _STATE["fail"]
if _STATE["fail"] == 0:
    print(f"OK  {_STATE['pass']}/{_total} tests passés")
    sys.exit(0)
else:
    print(f"XX  {_STATE['fail']} test(s) en échec sur {_total}")
    sys.exit(1)
