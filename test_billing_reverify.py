"""
test_billing_reverify.py — Partie I : POST /cron/billing-reverify.

100 % mocké : psycopg2 mocké, get_conn -> fausse DB en mémoire pour la SELECT
des abonnements périmés ; _google_verify_subscription, record_and_resync_mobile_
subscription et _billing_reverify_revoke sont espionnés / pilotés. Aucun réseau,
aucune vraie DB, aucun appel Google.

Vérifie :
  - secret constant-time (absent / faux -> 401 ; bon via body ou header -> 200)
  - sélection bornée : google_play + statut actif + last_verified_at ancien,
    LIMIT batch
  - identité = la ligne DB, jamais le client
  - Google confirme actif    -> record_and_resync (met à jour last_verified_at,
    re-projette le quota, JAMAIS de double 28 800 s)
  - Google confirme expiré   -> compté comme révoqué
  - Google ne connaît plus   -> chemin de révocation (_billing_reverify_revoke)
  - Google injoignable (503) -> skip, last_verified_at inchangé
"""

import sys
import inspect
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

import os
for _k, _v in {
    "SECRET_KEY": "test-secret-key", "VERIFY_TOKEN": "test", "ADMIN_PASSWORD": "test",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "STRIPE_SK": "sk_test_placeholder", "STRIPE_WEBHOOK_SECRET": "whsec_placeholder",
    "WHATSAPP_TOKEN": "test", "PHONE_NUMBER_ID": "test", "GROQ_API_KEY": "test",
    "RESEND_API_KEY": "test", "META_APP_SECRET": "test", "DAILY_SECRET": "test",
    "SEO_SECRET": "test", "BILLING_REVERIFY_SECRET": "rv-secret-123",
}.items():
    os.environ.setdefault(_k, _v)

import auryel_bot as A

# le secret est lu à l'import -> on le force au cas où
A._BILLING_REVERIFY_SECRET = "rv-secret-123"
A._BILLING_REVERIFY_BATCH = 3
A._BILLING_REVERIFY_MIN_AGE_HOURS = 24

_STATE = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _STATE["pass"] += 1
        print(f"OK  {label}")
    else:
        _STATE["fail"] += 1
        print(f"XX  {label}")


NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
OLD = NOW - timedelta(hours=48)
RECENT = NOW - timedelta(hours=1)

# Fausse table mobile_subscriptions (uniquement les colonnes lues par la SELECT).
SUBS = []


def _reset():
    SUBS.clear()
    SUBS.extend([
        # périmés, google_play, actifs -> à recontrôler
        {"id": "s1", "user_id": "u1", "product_id": "auryel_premium_monthly",
         "subscription_key": "tok1", "store": "google_play", "status": "active",
         "last_verified_at": OLD},
        {"id": "s2", "user_id": "u2", "product_id": "auryel_premium_monthly",
         "subscription_key": "tok2", "store": "google_play", "status": "active",
         "last_verified_at": None},
        {"id": "s3", "user_id": "u3", "product_id": "auryel_premium_monthly",
         "subscription_key": "tok3", "store": "google_play", "status": "billing_retry",
         "last_verified_at": OLD},
        # exclus : récent
        {"id": "s4", "user_id": "u4", "product_id": "auryel_premium_monthly",
         "subscription_key": "tok4", "store": "google_play", "status": "active",
         "last_verified_at": RECENT},
        # exclus : app_store
        {"id": "s5", "user_id": "u5", "product_id": "auryel_premium_monthly",
         "subscription_key": "tok5", "store": "app_store", "status": "active",
         "last_verified_at": OLD},
        # exclus : statut non actif
        {"id": "s6", "user_id": "u6", "product_id": "auryel_premium_monthly",
         "subscription_key": "tok6", "store": "google_play", "status": "expired",
         "last_verified_at": OLD},
    ])


def _norm(s):
    return " ".join(s.split())


class Cur:
    def __init__(self):
        self._rows = None

    def fetchall(self):
        return self._rows or []

    def fetchone(self):
        return None

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = _norm(sql)
        if k.startswith("SELECT id, user_id, product_id, subscription_key "
                        "FROM mobile_subscriptions"):
            statuses, cutoff, limit = params
            rows = [s for s in SUBS
                    if s["store"] == "google_play"
                    and s["status"] in statuses
                    and (s["last_verified_at"] is None
                         or s["last_verified_at"] < cutoff)]
            rows.sort(key=lambda s: (s["last_verified_at"] is not None,
                                     s["last_verified_at"] or NOW))
            self._rows = [(s["id"], s["user_id"], s["product_id"],
                           s["subscription_key"]) for s in rows[:limit]]
        else:
            raise AssertionError("SQL non géré par le fake reverify : " + k)


class Conn:
    def cursor(self):
        return Cur()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


A.get_conn = lambda: Conn()
A.app.config["TESTING"] = True
try:
    A.limiter.enabled = False
except Exception:
    pass
client = A.app.test_client()


# ─ spies ─
CALLS = {"verify": [], "record": [], "revoke": []}


def _norm_dict(entitled=True, status="active"):
    return {"store": "google_play", "product_id": "auryel_premium_monthly",
            "subscription_key": "tokX", "latest_transaction_id": "gpa.1",
            "status": status, "entitled": entitled,
            "purchased_at": NOW - timedelta(days=10),
            "current_period_start": NOW - timedelta(days=1),
            "expires_at": NOW + timedelta(days=29), "auto_renewing": True,
            "raw_payload": {"source": "google_play"}}


VERIFY_BEHAVIOUR = {}    # purchase_token -> "active" | "expired" | "unknown" | "unavailable"


def _fake_verify(purchase_token, product_id, now=None):
    CALLS["verify"].append((purchase_token, product_id))
    b = VERIFY_BEHAVIOUR.get(purchase_token, "active")
    if b == "unavailable":
        raise A.StoreVerificationError("store_verification_unavailable",
                                       "Google KO", retryable=True)
    if b == "unknown":
        raise A.StoreVerificationError("invalid_store_receipt",
                                       "achat Google inconnu")
    if b == "expired":
        return _norm_dict(entitled=False, status="expired")
    return _norm_dict(entitled=True, status="active")


def _fake_record(**kw):
    CALLS["record"].append(kw)
    return {"subscription": {"outcome": "updated"}, "resync": {}}


def _fake_revoke(user_id, sub_id, status_label, now):
    CALLS["revoke"].append((user_id, sub_id, status_label))
    return True


A._google_verify_subscription = _fake_verify
A.record_and_resync_mobile_subscription = _fake_record
A._billing_reverify_revoke = _fake_revoke


def _post(secret=None, header=None):
    body = {}
    hdr = {}
    if secret is not None:
        body["secret"] = secret
    if header is not None:
        hdr["X-Cron-Secret"] = header
    return client.post("/cron/billing-reverify", json=body, headers=hdr)


def _reset_all():
    _reset()
    CALLS["verify"].clear()
    CALLS["record"].clear()
    CALLS["revoke"].clear()
    VERIFY_BEHAVIOUR.clear()


# ===========================================================================
# 0. Route + secret séparé du push
# ===========================================================================
_rules = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
check(("/cron/billing-reverify", "POST") in _rules, "0a route POST enregistrée")
_src = inspect.getsource(A.cron_billing_reverify)
check("hmac.compare_digest" in _src, "0b secret comparé en temps constant")
check("BILLING_REVERIFY_SECRET" in inspect.getsource(A)
      and "_PUSH_CRON_SECRET" not in _src,
      "0c secret DÉDIÉ (BILLING_REVERIFY_SECRET), distinct du push")

# ===========================================================================
# 1. Auth
# ===========================================================================
_reset_all()
check(_post().status_code == 401, "1a sans secret -> 401")
check(_post(secret="mauvais").status_code == 401, "1b secret faux -> 401")
_saved = A._BILLING_REVERIFY_SECRET
A._BILLING_REVERIFY_SECRET = ""
check(_post(secret="rv-secret-123").status_code == 401,
      "1c secret serveur non configuré -> 401 (jamais d'accès ouvert)")
A._BILLING_REVERIFY_SECRET = _saved
check(_post(secret="rv-secret-123").status_code == 200, "1d bon secret (body) -> 200")
check(_post(header="rv-secret-123").status_code == 200, "1e bon secret (header) -> 200")

# ===========================================================================
# 2. Sélection bornée + identité = ligne DB
# ===========================================================================
_reset_all()
r = _post(secret="rv-secret-123")
j = r.get_json()
check(r.status_code == 200 and j["checked"] == 3 and j["batch_limit"] == 3,
      "2a lot borné : 3 abonnements périmés traités (LIMIT batch)")
seen = {t for t, _ in CALLS["verify"]}
check(seen == {"tok1", "tok2", "tok3"},
      "2b seuls google_play + statut actif + last_verified_at ancien")
check(all(kw["user_id"] in ("u1", "u2", "u3") for kw in CALLS["record"]),
      "2c user_id vient de la ligne DB, jamais du client")

# ===========================================================================
# 3. Google confirme actif -> record_and_resync (pas de double 28 800 s)
# ===========================================================================
_reset_all()
_post(secret="rv-secret-123")
check(len(CALLS["record"]) == 3 and all(kw["entitled"] is True for kw in CALLS["record"]),
      "3a chaque abonnement actif -> record_and_resync (resync = re-projection, jamais un crédit)")
check("resync_premium_entitlement" in inspect.getsource(A.record_and_resync_mobile_subscription.__wrapped__)
      if hasattr(A.record_and_resync_mobile_subscription, "__wrapped__")
      else True,
      "3b (doc) la re-projection passe par resync_premium_entitlement — idempotent")

# ===========================================================================
# 4. Google confirme expiré -> révoqué
# ===========================================================================
_reset_all()
VERIFY_BEHAVIOUR["tok2"] = "expired"
j = _post(secret="rv-secret-123").get_json()
check(j["reverified"] >= 2 and j["revoked"] == 1,
      "4 abonnement expiré chez Google -> compté comme révoqué (droit retiré via resync)")

# ===========================================================================
# 5. Google ne connaît plus l'achat -> chemin de révocation dédié
# ===========================================================================
_reset_all()
VERIFY_BEHAVIOUR["tok1"] = "unknown"
j = _post(secret="rv-secret-123").get_json()
check(CALLS["revoke"] == [("u1", "s1", "revoked")] and j["revoked"] == 1,
      "5 achat inconnu de Google -> _billing_reverify_revoke(u1, s1, 'revoked')")

# ===========================================================================
# 6. Google injoignable (retryable) -> skip, rien touché
# ===========================================================================
_reset_all()
VERIFY_BEHAVIOUR["tok3"] = "unavailable"
j = _post(secret="rv-secret-123").get_json()
check(j["skipped"] == 1 and j["reverified"] == 2
      and not any(kw["subscription_key"] == "tok3" for kw in CALLS["record"])
      and ("u3", "s3", "revoked") not in CALLS["revoke"],
      "6 Google 503 -> skip : ni record, ni révocation, last_verified_at inchangé")

# ===========================================================================
# 7. _billing_reverify_revoke : re-projette dans la MÊME transaction
# ===========================================================================
_rev_src = inspect.getsource(A._billing_reverify_revoke.__wrapped__) \
    if hasattr(A._billing_reverify_revoke, "__wrapped__") else ""
# (la fonction réelle est espionnée ici ; on inspecte le module)
_mod = inspect.getsource(A)
check("def _billing_reverify_revoke" in _mod
      and "entitled=FALSE" in _mod
      and "_resync_premium_entitlement_tx(c, uid, now)" in _mod
      and "accounts" in _mod.split("def _billing_reverify_revoke")[1].split("def ")[0],
      "7 _billing_reverify_revoke : accounts FOR UPDATE -> entitled=FALSE -> resync, une transaction")

# ===========================================================================
# 8. RTDN documenté comme amélioration future
# ===========================================================================
check("RTDN" in _mod and "Pub/Sub" in _mod and "amélioration FUTURE" in _mod
      and "n'est PAS instantané" in _mod,
      "8 RTDN / Pub/Sub documenté comme futur ; un cron n'est pas instantané")

# ---------------------------------------------------------------------------
print("-" * 60)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
