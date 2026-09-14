"""
test_express_consultation_api.py — GROS CHANTIER AURYEL (Prompt 3/5) :
route HTTP POST /api/app/rewards/express-consultation.

`purchase_express_consultation` (le moteur) est déjà testé exhaustivement
par test_express_consultation.py — ce fichier teste la COUCHE HTTP : contrat
JSON, validation du body, auth, et qu'AUCUN montant/coût/durée fourni par le
client n'est jamais lu.

100 % local : psycopg2 mocké, FakeConn/FakeCursor en mémoire, resolve_app_session
monkeypatché. Aucun réseau, aucune vraie DB, aucun LLM.
"""

import sys
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


NOW = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)
A._utcnow = lambda: NOW

UID1 = "11111111-1111-4111-8111-111111111111"

_ACCOUNTS = {}
_WALLET = {}
_PRODUCTS = {"express_consultation_10min": {"stars_cost": 500, "seconds_granted": 600,
                                            "enabled": True}}
_EXPRESS = []
_TX = []
_LEDGER = []


def _reset():
    _ACCOUNTS.clear()
    _WALLET.clear()
    _EXPRESS.clear()
    _TX.clear()
    _LEDGER.clear()
    _ACCOUNTS[UID1] = {"deleted_at": None, "first_free_seconds_remaining": 0,
                       "earned_seconds_remaining": 0, "purchased_seconds_remaining": 0}
    _WALLET[UID1] = {"stars_balance": 1000}


class _Cur:
    def __init__(self):
        self._r = None

    def fetchone(self):
        return self._r

    def close(self):
        pass

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        p = params or ()
        self._r = None

        if "COALESCE(SUM(seconds_granted), 0) FROM express_consultations" in s:
            self._r = (sum(
                e["seconds_granted"] for e in _EXPRESS if e["user_id"] == p[0]
            ),)
        elif "FROM accounts WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE" in s:
            acc = _ACCOUNTS.get(p[0])
            self._r = (p[0],) if acc and acc["deleted_at"] is None else None
        elif "stars_spent, seconds_granted, status FROM express_consultations" in s:
            uid, idem = p
            m = next((e for e in _EXPRESS if e["user_id"] == uid and e["idem"] == idem), None)
            self._r = (m["stars_spent"], m["seconds_granted"], "completed") if m else None
        elif "SELECT stars_cost, seconds_granted, enabled FROM express_products" in s:
            prod = _PRODUCTS.get(p[0])
            self._r = (prod["stars_cost"], prod["seconds_granted"], prod["enabled"]) if prod else None
        elif "INSERT INTO express_consultations" in s:
            eid, uid, spent, granted, idem, created_at = p
            _EXPRESS.append({"user_id": uid, "stars_spent": spent,
                             "seconds_granted": granted, "idem": idem})
        elif "INSERT INTO reward_wallet (user_id, stars_balance, updated_at)" in s:
            _WALLET.setdefault(p[0], {"stars_balance": 0})
        elif "SELECT stars_balance FROM reward_wallet WHERE user_id=%s FOR UPDATE" in s:
            w = _WALLET.setdefault(p[0], {"stars_balance": 0})
            self._r = (w["stars_balance"],)
        elif "SELECT stars_balance FROM reward_wallet WHERE user_id=%s" in s:
            w = _WALLET.get(p[0])
            self._r = (w["stars_balance"] if w else 0,)
        elif "SELECT balance_after FROM reward_transactions" in s:
            uid, idem = p
            m = next((t for t in _TX if t["uid"] == uid and t["idem"] == idem), None)
            self._r = (m["balance_after"],) if m else None
        elif "INSERT INTO reward_transactions" in s and "'spend'" in s:
            tid, uid, delta, balance_after, reason, idem, created_at = p
            if not any(t["uid"] == uid and t["idem"] == idem for t in _TX):
                _TX.append({"uid": uid, "idem": idem, "balance_after": balance_after})
                self._r = (tid,)
        elif "UPDATE reward_wallet SET stars_balance=%s, updated_at=%s" in s:
            new_balance, now, uid = p
            _WALLET.setdefault(uid, {"stars_balance": 0})["stars_balance"] = new_balance
        elif "INSERT INTO time_ledger" in s and "idempotency_key" in s:
            lid, uid, amount, reason, key, created_at = p
            if not any(e["uid"] == uid and e["key"] == key for e in _LEDGER):
                _LEDGER.append({"uid": uid, "key": key})
                self._r = (lid,)
        elif "SELECT COALESCE(earned_seconds_remaining, 0) FROM accounts" in s:
            self._r = (int(_ACCOUNTS[p[0]].get("earned_seconds_remaining") or 0),)
        elif "UPDATE accounts SET earned_seconds_remaining" in s:
            amount, uid = p
            _ACCOUNTS[uid]["earned_seconds_remaining"] = \
                (_ACCOUNTS[uid].get("earned_seconds_remaining") or 0) + amount
            self._r = (_ACCOUNTS[uid]["earned_seconds_remaining"],)
        elif ("SELECT first_free_seconds_remaining, earned_seconds_remaining, "
              "purchased_seconds_remaining") in s:
            acc = _ACCOUNTS[p[0]]
            self._r = (acc["first_free_seconds_remaining"], acc["earned_seconds_remaining"],
                       acc["purchased_seconds_remaining"])
        elif "monthly_allowance_seconds, monthly_used_seconds" in s:
            self._r = None
        else:
            raise AssertionError("SQL non modélisé (express api) : " + s)


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

A.resolve_app_session = lambda tok: (
    {"session_id": "s", "user_id": UID1, "email": "x@x.co"} if tok else None
)


def _hdr(tok="x"):
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _purchase(body, tok="x"):
    return client.post("/api/app/rewards/express-consultation", headers=_hdr(tok), json=body)


# ===========================================================================
# 0. Route enregistrée
# ===========================================================================
_rules = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
check(("/api/app/rewards/express-consultation", "POST") in _rules,
      "0 route POST /api/app/rewards/express-consultation enregistrée")

# ===========================================================================
# 1. Auth obligatoire
# ===========================================================================
_reset()
check(_purchase({"product_key": "express_consultation_10min",
                 "idempotency_key": "k1"}, tok=None).status_code == 401,
      "1 sans Bearer -> 401")

# ===========================================================================
# 2. Body invalide
# ===========================================================================
_reset()
check(_purchase({}).status_code == 400, "2a body vide -> 400")
check(_purchase({"product_key": "express_consultation_10min"}).status_code == 400,
      "2b idempotency_key manquante -> 400")
check(_purchase({"idempotency_key": "k1"}).status_code == 400,
      "2c product_key manquant -> 400")
check(_purchase({"product_key": "", "idempotency_key": "k1"}).status_code == 400,
      "2d product_key vide -> 400")

# ===========================================================================
# 3. Achat réussi — contrat de réponse exact
# ===========================================================================
_reset()
r = _purchase({"product_key": "express_consultation_10min", "idempotency_key": "k1"})
j = r.get_json()
check(r.status_code == 200, "3a 200 OK")
check(j["success"] is True, "3b success=True")
check(j["stars_spent"] == 500, "3c stars_spent=500")
check(j["stars_balance"] == 500, "3d stars_balance=500 (1000-500)")
check(j["seconds_granted"] == 600, "3e seconds_granted=600")
check("balances" in j and isinstance(j["balances"], dict), "3f balances présent")

# ===========================================================================
# 4. AUCUN montant/coût/durée fourni par le client n'est jamais lu
# ===========================================================================
_reset()
r = _purchase({
    "product_key": "express_consultation_10min", "idempotency_key": "k2",
    "stars_cost": 1, "seconds_granted": 999999, "delta_stars": -1, "delta_seconds": 99999,
})
j = r.get_json()
check(j["success"] is True and j["stars_spent"] == 500 and j["seconds_granted"] == 600,
      "4 champs client (stars_cost/seconds_granted/delta_*) totalement IGNORÉS — "
      "le serveur résout tout depuis express_products")

# ===========================================================================
# 5. Solde insuffisant -> 200 (pas une erreur HTTP), success=False
# ===========================================================================
_reset()
_WALLET[UID1]["stars_balance"] = 100
r = _purchase({"product_key": "express_consultation_10min", "idempotency_key": "k3"})
j = r.get_json()
check(r.status_code == 200 and j["success"] is False
      and j["reason"] == "insufficient_balance",
      "5 solde insuffisant -> 200, success=False, reason=insufficient_balance "
      "(état métier normal, jamais une erreur HTTP)")

# ===========================================================================
# 6. Compte supprimé -> 401
# ===========================================================================
_reset()
_ACCOUNTS[UID1]["deleted_at"] = NOW
r = _purchase({"product_key": "express_consultation_10min", "idempotency_key": "k4"})
check(r.status_code == 401, "6 compte supprimé -> 401")

# ===========================================================================
# 7. Idempotence via HTTP — double tap / retry
# ===========================================================================
_reset()
r1 = _purchase({"product_key": "express_consultation_10min", "idempotency_key": "k5"})
r2 = _purchase({"product_key": "express_consultation_10min", "idempotency_key": "k5"})
check(r1.get_json()["stars_balance"] == 500 and r2.get_json()["stars_balance"] == 500,
      "7 rejeu HTTP de la même idempotency_key -> même résultat, aucun 2e débit")
check(len(_EXPRESS) == 1, "7b une seule ligne express_consultations")

print("-" * 64)
total = _STATE["pass"] + _STATE["fail"]
print(f"RESULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko (sur {total})")
sys.exit(1 if _STATE["fail"] else 0)
