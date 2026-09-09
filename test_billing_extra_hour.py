"""
test_billing_extra_hour.py — v44 : achat CONSOMMABLE « +1 heure supplémentaire »
(produit Google Play `auryel_extra_hour`).

Couvre :
  - _google_verify_product : purchaseState 0 / 1 / 2, 404, parsing, pas de token
    dans raw_payload
  - credit_consumable_purchase : +3600 dans 'purchased', anti-double-crédit,
    2 achats distincts = +7200, earned / Premium / first_free intacts,
    account_mismatch, mapping serveur figé
  - POST /api/billing/purchase : contrat de route (auth, validation body,
    dispatch, mapping erreurs, forme réponse, Premium non touché)

AUCUN réseau : _google_api_request est mocké. DB = FakeConn en mémoire
(psycopg2 mocké). Script (pas pytest) : sys.exit(1) si un test échoue.
"""

import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.modules["psycopg2"] = MagicMock()

import os
for _k, _v in {
    "SECRET_KEY": "t", "VERIFY_TOKEN": "t", "ADMIN_PASSWORD": "t", "CRON_SECRET": "t",
    "DATABASE_URL": "postgresql://t:t@localhost/t", "STRIPE_SK": "t",
    "STRIPE_WEBHOOK_SECRET": "t", "WHATSAPP_TOKEN": "t", "PHONE_NUMBER_ID": "t",
    "GROQ_API_KEY": "t", "RESEND_API_KEY": "t", "META_APP_SECRET": "t",
    "DAILY_SECRET": "t", "SEO_SECRET": "t", "TAROT_MEDIA_UPLOAD_DISABLED": "1",
}.items():
    os.environ.setdefault(_k, _v)

os.environ["GOOGLE_PLAY_PACKAGE_NAME"] = "com.auryel.auryel"
os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = '{"type":"service_account","project_id":"x"}'

import auryel_bot as A

_S = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _S["pass"] += 1
        print(f"✅ {label}")
    else:
        _S["fail"] += 1
        print(f"❌ {label}")


NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
A._utcnow = lambda: NOW

UID = "11111111-1111-4111-8111-111111111111"
UID2 = "22222222-2222-4222-8222-222222222222"
PID = "auryel_extra_hour"

# ---------------------------------------------------------------------------
# FakeConn : modélise UNIQUEMENT le SQL de credit_consumable_purchase +
# get_consultation_state (par sous-chaîne).
# ---------------------------------------------------------------------------
DB = {"accounts": {}, "mobile_purchases": [], "time_ledger": [],
      "mobile_subscriptions": [], "consultation_allowance": [], "earned_credits": []}


def reset_db():
    DB["accounts"].clear()
    DB["mobile_purchases"].clear()
    DB["time_ledger"].clear()
    DB["mobile_subscriptions"].clear()
    DB["consultation_allowance"].clear()
    DB["earned_credits"].clear()


def seed_account(uid, deleted=False, purchased=0, earned=0, first_free=3600):
    DB["accounts"][uid] = {
        "user_id": uid, "deleted_at": (NOW if deleted else None),
        "purchased_seconds_remaining": purchased,
        "earned_seconds_remaining": earned,
        "first_free_seconds_remaining": first_free,
        "first_consultation_used_at": None,
    }


class FakeCursor:
    def __init__(self):
        self._r = None
        self._rows = None
        self.rowcount = -1

    def fetchone(self):
        return self._r

    def fetchall(self):
        return list(self._rows or [])

    def close(self):
        pass

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        p = params or ()
        self.rowcount = -1
        self._r = None
        self._rows = None

        if "FROM accounts WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE" in s:
            row = DB["accounts"].get(p[0])
            self._r = (row["user_id"],) if row and row["deleted_at"] is None else None

        elif "INSERT INTO mobile_purchases" in s:
            (row_id, uid, store, product_id, key, order_id, seconds,
             purchased_at, credited_at, payload, created_at) = p
            exists = any(r["store"] == store and r["purchase_key"] == key
                         for r in DB["mobile_purchases"])
            if exists:
                self.rowcount = 0
            else:
                DB["mobile_purchases"].append(dict(
                    id=str(row_id), user_id=str(uid), store=store,
                    product_id=product_id, purchase_key=key, order_id=order_id,
                    credited_seconds=seconds, status="credited",
                    purchased_at=purchased_at, credited_at=credited_at,
                    raw_payload=payload, created_at=created_at))
                self.rowcount = 1

        elif "UPDATE accounts SET purchased_seconds_remaining" in s:
            seconds, uid = p
            DB["accounts"][uid]["purchased_seconds_remaining"] = (
                (DB["accounts"][uid].get("purchased_seconds_remaining") or 0) + seconds)
            self.rowcount = 1

        elif "INSERT INTO time_ledger" in s:
            (lid, uid, bucket, delta, reason, ref_id, created_at) = p
            DB["time_ledger"].append(dict(
                id=str(lid), user_id=str(uid), bucket=bucket, delta_seconds=delta,
                reason=reason, ref_id=ref_id, created_at=created_at))
            self.rowcount = 1

        elif "SELECT user_id FROM mobile_purchases WHERE store=%s AND purchase_key=%s" in s:
            store, key = p
            r = next((r for r in DB["mobile_purchases"]
                      if r["store"] == store and r["purchase_key"] == key), None)
            self._r = (r["user_id"],) if r else None

        elif "SELECT COALESCE(purchased_seconds_remaining, 0) FROM accounts" in s:
            self._r = (int(DB["accounts"][p[0]].get("purchased_seconds_remaining") or 0),)

        # --- get_consultation_state (minimal) ---
        elif "FROM consultations WHERE user_id=%s AND expires_at > %s" in s:
            self._r = None
        elif ("SELECT period_start, period_end, monthly_limit, monthly_used "
              "FROM consultation_allowance") in s:
            rows = [a for a in DB["consultation_allowance"] if a["user_id"] == p[0]]
            self._r = ((rows[0]["period_start"], rows[0]["period_end"],
                        rows[0]["monthly_limit"], rows[0]["monthly_used"])
                       if rows else None)
        elif "SELECT COUNT(*) FROM earned_credits WHERE user_id=%s AND consumed_at IS NULL" in s:
            self._r = (len([e for e in DB["earned_credits"]
                            if e["user_id"] == p[0] and e.get("consumed_at") is None]),)
        elif "SELECT first_consultation_used_at FROM accounts WHERE user_id=%s" in s:
            row = DB["accounts"].get(p[0])
            self._r = (row["first_consultation_used_at"],) if row else None

        else:
            raise AssertionError("SQL non modélisé (v44) : " + s)


class FakeConn:
    def cursor(self):
        return FakeCursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


A.get_conn = lambda: FakeConn()

# ---------------------------------------------------------------------------
# 1. _google_verify_product
# ---------------------------------------------------------------------------
print("=" * 64)
print("1. _google_verify_product")
print("=" * 64)


_NOW_MILLIS = str(int(NOW.timestamp() * 1000))


def gp_product(purchase_state=0, consumption_state=0, ack=0,
               order_id="GPA.1111-2222-3333-44444",
               purchase_time=_NOW_MILLIS, ptype=None, region="FR"):
    body = {
        "kind": "androidpublisher#productPurchase",
        "purchaseState": purchase_state,
        "consumptionState": consumption_state,
        "acknowledgementState": ack,
        "orderId": order_id,
        "purchaseTimeMillis": purchase_time,
        "regionCode": region,
    }
    if ptype is not None:
        body["purchaseType"] = ptype
    return body


def _mock_req(status, body):
    return patch.object(A, "_google_api_request", return_value=(status, body))


with _mock_req(200, gp_product(purchase_state=0)):
    n = A._google_verify_product("tok-A", PID)
check(n["store"] == "google_play" and n["product_id"] == PID
      and n["purchase_key"] == "tok-A"
      and n["order_id"] == "GPA.1111-2222-3333-44444",
      "1a purchaseState=0 -> structure normalisée")
check(n["purchased_at"] == datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc),
      "1b purchaseTimeMillis -> purchased_at UTC")
check("tok-A" not in str(n["raw_payload"]),
      "1c raw_payload ne contient PAS le purchase_token")
check(n["raw_payload"]["purchase_state"] == 0
      and n["raw_payload"]["consumption_state"] == 0,
      "1d raw_payload = sous-ensemble non sensible")

with _mock_req(200, gp_product(purchase_state=1)):
    try:
        A._google_verify_product("tok-B", PID)
        check(False, "1e purchaseState=1 (annulé/remboursé) -> refus")
    except A.StoreVerificationError as e:
        check(e.code == "invalid_store_receipt" and not e.retryable,
              "1e purchaseState=1 (annulé/remboursé) -> invalid_store_receipt (non retryable)")

with _mock_req(200, gp_product(purchase_state=2)):
    try:
        A._google_verify_product("tok-C", PID)
        check(False, "1f purchaseState=2 (pending) -> refus")
    except A.StoreVerificationError as e:
        check(e.code == "store_verification_unavailable" and e.retryable,
              "1f purchaseState=2 (pending) -> store_verification_unavailable (retryable)")

with _mock_req(404, {"error": {"message": "not found"}}):
    try:
        A._google_verify_product("tok-D", PID)
        check(False, "1g 404 -> refus")
    except A.StoreVerificationError as e:
        check(e.code == "invalid_store_receipt", "1g token inconnu (404) -> invalid_store_receipt")

with _mock_req(503, None):
    try:
        A._google_verify_product("tok-E", PID)
        check(False, "1h 503 -> refus")
    except A.StoreVerificationError as e:
        check(e.code == "store_verification_unavailable" and e.retryable,
              "1h Google 5xx -> store_verification_unavailable (retryable)")

with _mock_req(200, gp_product(purchase_state=0, consumption_state=1)):
    n = A._google_verify_product("tok-F", PID)
check(n["purchase_key"] == "tok-F",
      "1i consumptionState=1 (déjà consommé par le client) -> accepté quand même")

# ---------------------------------------------------------------------------
# 2. credit_consumable_purchase
# ---------------------------------------------------------------------------
print("=" * 64)
print("2. credit_consumable_purchase")
print("=" * 64)

reset_db()
seed_account(UID, purchased=0, earned=1800, first_free=3600)
DB["consultation_allowance"].append(dict(
    user_id=UID, period_start=NOW.replace(hour=0), period_end=NOW.replace(day=30),
    monthly_limit=8, monthly_used=2))

r1 = A.credit_consumable_purchase(UID, "google_play", PID, "tok-1",
                                  order_id="O-1", raw_payload={"source": "google_play"})
check(r1["credited"] is True and r1["credited_seconds"] == 3600
      and r1["already_credited"] is False
      and r1["purchased_seconds_remaining"] == 3600,
      "2a achat valide -> +3600 dans 'purchased'")
check(DB["accounts"][UID]["purchased_seconds_remaining"] == 3600,
      "2b accounts.purchased_seconds_remaining == 3600")
check(DB["accounts"][UID]["earned_seconds_remaining"] == 1800,
      "2c earned INCHANGÉ")
check(DB["accounts"][UID]["first_free_seconds_remaining"] == 3600,
      "2d first_free INCHANGÉ")
check(DB["consultation_allowance"][0]["monthly_used"] == 2
      and DB["consultation_allowance"][0]["monthly_limit"] == 8,
      "2e quota Premium (consultation_allowance) INCHANGÉ")
check(len(DB["mobile_subscriptions"]) == 0, "2f mobile_subscriptions INTACT (0 ligne)")
led = [x for x in DB["time_ledger"] if x["reason"] == "purchase_extra_hour"]
check(len(led) == 1 and led[0]["bucket"] == "purchased" and led[0]["delta_seconds"] == 3600,
      "2g time_ledger : 1 ligne purchased +3600 reason=purchase_extra_hour")
check(led[0]["ref_id"] == DB["mobile_purchases"][0]["id"],
      "2h ledger.ref_id = mobile_purchases.id (pas le token)")

# rejeu du MÊME token -> aucun double crédit
r2 = A.credit_consumable_purchase(UID, "google_play", PID, "tok-1")
check(r2["credited"] is False and r2["credited_seconds"] == 0
      and r2["already_credited"] is True
      and r2["purchased_seconds_remaining"] == 3600,
      "2i rejeu du même token -> AUCUN double crédit (already_credited)")
check(DB["accounts"][UID]["purchased_seconds_remaining"] == 3600
      and len(DB["mobile_purchases"]) == 1
      and len([x for x in DB["time_ledger"] if x["reason"] == "purchase_extra_hour"]) == 1,
      "2j rejeu : 1 seule ligne mobile_purchases, 1 seule ligne ledger")

# 2e achat DISTINCT (nouveau token) -> +3600 supplémentaires
r3 = A.credit_consumable_purchase(UID, "google_play", PID, "tok-2")
check(r3["credited"] is True and r3["purchased_seconds_remaining"] == 7200,
      "2k 2e achat distinct (tok-2) -> +3600 => 7200 (rachat OK)")
check(len(DB["mobile_purchases"]) == 2, "2l 2 lignes mobile_purchases distinctes")

# mauvais product_id -> invalid_product
try:
    A.credit_consumable_purchase(UID, "google_play", "auryel_premium_monthly", "tok-x")
    check(False, "2m mauvais product_id -> refus")
except A.MobileSubscriptionError as e:
    check(e.code == "invalid_product", "2m product_id hors mapping -> invalid_product")

# store inconnu -> invalid_store
try:
    A.credit_consumable_purchase(UID, "amazon", PID, "tok-y")
    check(False, "2n store inconnu -> refus")
except A.MobileSubscriptionError as e:
    check(e.code == "invalid_store", "2n store hors liste -> invalid_store")

# token vide -> missing_field
try:
    A.credit_consumable_purchase(UID, "google_play", PID, "   ")
    check(False, "2o token vide -> refus")
except A.MobileSubscriptionError as e:
    check(e.code == "missing_field", "2o purchase_key vide -> missing_field")

# token déjà rattaché à un AUTRE compte -> account_mismatch
seed_account(UID2)
try:
    A.credit_consumable_purchase(UID2, "google_play", PID, "tok-1")
    check(False, "2p token d'un autre compte -> refus")
except A.MobileSubscriptionError as e:
    check(e.code == "account_mismatch", "2p token déjà rattaché ailleurs -> account_mismatch")
check(DB["accounts"][UID2]["purchased_seconds_remaining"] == 0,
      "2q compte tiers : aucun crédit")

# compte supprimé -> account_mismatch
seed_account(UID2, deleted=True)
try:
    A.credit_consumable_purchase(UID2, "google_play", PID, "tok-z")
    check(False, "2r compte supprimé -> refus")
except A.MobileSubscriptionError as e:
    check(e.code == "account_mismatch", "2r compte deleted_at -> account_mismatch")

# mapping serveur figé : la durée ne vient jamais d'un argument
check(A._CONSUMABLE_PRODUCT_SECONDS == {"auryel_extra_hour": 3600},
      "2s mapping serveur figé auryel_extra_hour => 3600")

# ---------------------------------------------------------------------------
# 3. POST /api/billing/purchase
# ---------------------------------------------------------------------------
print("=" * 64)
print("3. POST /api/billing/purchase")
print("=" * 64)

A.app.config["TESTING"] = True
A.app.config["RATELIMIT_ENABLED"] = False
A.limiter.enabled = False
client = A.app.test_client()
GOOD = "good-bearer"
A.resolve_app_session = lambda tok: (
    {"session_id": "s", "user_id": UID, "email": "u@x"} if tok == GOOD else None)
A._google_acknowledge_product = lambda *a, **k: {"acknowledged": True}

H = {"Authorization": "Bearer " + GOOD}


def post(body, headers=H):
    return client.post("/api/billing/purchase", json=body, headers=headers)


# 3a auth requise
reset_db()
seed_account(UID)
r = post({"store": "google_play", "product_id": PID, "purchase_token": "pt-1"},
         headers={"Authorization": "Bearer nope"})
check(r.status_code == 401, "3a sans session valide -> 401")

# 3b body non-dict
r = client.post("/api/billing/purchase", data="x", headers=H)
check(r.status_code == 400, "3b body illisible -> 400")

# 3c store invalide
r = post({"store": "steam", "product_id": PID, "purchase_token": "pt"})
check(r.status_code == 400 and r.get_json()["error"] == "invalid_store",
      "3c store hors liste -> 400 invalid_store")

# 3d product_id invalide (abonnement rejeté par cette route)
r = post({"store": "google_play", "product_id": "auryel_premium_monthly",
          "purchase_token": "pt"})
check(r.status_code == 400 and r.get_json()["error"] == "invalid_product",
      "3d product_id non consommable -> 400 invalid_product")

# 3e app_store -> 422 (lot ultérieur)
r = post({"store": "app_store", "product_id": PID, "transaction_id": "tx"})
check(r.status_code == 422 and r.get_json()["error"] == "invalid_store_receipt",
      "3e app_store consommable -> 422 invalid_store_receipt")

# 3f purchase_token manquant
r = post({"store": "google_play", "product_id": PID})
check(r.status_code == 400 and r.get_json()["error"] == "missing_purchase_token",
      "3f purchase_token absent -> 400 missing_purchase_token")


def _norm(key="pt-1"):
    return {"store": "google_play", "product_id": PID, "purchase_key": key,
            "order_id": "O-9", "purchased_at": NOW,
            "raw_payload": {"source": "google_play",
                            "acknowledgement_state": 0}}


A.get_consultation_state = lambda uid, now=None: {
    "consultation": None,
    "quota": {"is_premium": False, "period_start": None, "period_end": None,
              "monthly_limit": 0, "monthly_used": 0, "monthly_remaining": 0,
              "earned_available": 0, "first_free_available": True},
}

# 3g achat valide -> 200, +3600, réponse conforme
reset_db()
seed_account(UID, purchased=0, earned=900)
with patch.object(A, "_google_verify_product", return_value=_norm("pt-1")):
    r = post({"store": "google_play", "product_id": PID, "purchase_token": "pt-1"})
j = r.get_json()
check(r.status_code == 200 and j["purchase"]["credited_seconds"] == 3600
      and j["purchase"]["already_credited"] is False
      and j["purchase"]["product_id"] == PID
      and "quota" in j,
      "3g achat valide -> 200 {purchase:+3600, quota}")
check(DB["accounts"][UID]["purchased_seconds_remaining"] == 3600
      and DB["accounts"][UID]["earned_seconds_remaining"] == 900,
      "3h crédit dans 'purchased', earned intact")
check(r.headers.get("Cache-Control") == "no-store",
      "3i réponse Cache-Control: no-store")
check("pt-1" not in r.get_data(as_text=True),
      "3j la réponse ne renvoie pas le purchase_token")

# 3k rejeu même token -> 200 already_credited, pas de double
with patch.object(A, "_google_verify_product", return_value=_norm("pt-1")):
    r = post({"store": "google_play", "product_id": PID, "purchase_token": "pt-1"})
j = r.get_json()
check(r.status_code == 200 and j["purchase"]["already_credited"] is True
      and j["purchase"]["credited_seconds"] == 0
      and DB["accounts"][UID]["purchased_seconds_remaining"] == 3600,
      "3k rejeu -> 200 already_credited, purchased reste 3600")

# 3l 2e achat distinct -> +3600
with patch.object(A, "_google_verify_product", return_value=_norm("pt-2")):
    r = post({"store": "google_play", "product_id": PID, "purchase_token": "pt-2"})
check(r.status_code == 200 and DB["accounts"][UID]["purchased_seconds_remaining"] == 7200,
      "3l 2e token distinct -> +3600 => 7200")

# 3m store indisponible -> 503
reset_db()
seed_account(UID)
with patch.object(A, "_google_verify_product",
                  side_effect=A.StoreVerificationError("store_verification_unavailable",
                                                       "x", retryable=True)):
    r = post({"store": "google_play", "product_id": PID, "purchase_token": "pt-1"})
check(r.status_code == 503, "3m verify retryable -> 503")
check(DB["accounts"][UID]["purchased_seconds_remaining"] == 0, "3n 503 -> aucun crédit")

# 3o reçu invalide (annulé/remboursé) -> 422
with patch.object(A, "_google_verify_product",
                  side_effect=A.StoreVerificationError("invalid_store_receipt", "x")):
    r = post({"store": "google_play", "product_id": PID, "purchase_token": "pt-9"})
check(r.status_code == 422, "3o verify fatal -> 422")
check(DB["accounts"][UID]["purchased_seconds_remaining"] == 0, "3p 422 -> aucun crédit")

# 3q token d'un autre compte -> 409
reset_db()
seed_account(UID)
seed_account(UID2)
DB["mobile_purchases"].append(dict(store="google_play", purchase_key="pt-shared",
                                   user_id=UID2))
with patch.object(A, "_google_verify_product", return_value=_norm("pt-shared")):
    r = post({"store": "google_play", "product_id": PID, "purchase_token": "pt-shared"})
check(r.status_code == 409 and r.get_json()["error"] == "account_mismatch",
      "3q token rattaché à un autre compte -> 409 account_mismatch")
check(DB["accounts"][UID]["purchased_seconds_remaining"] == 0, "3r 409 -> aucun crédit")

# 3s GET non autorisé
r = client.get("/api/billing/purchase", headers=H)
check(r.status_code == 405, "3s GET /api/billing/purchase -> 405 (POST uniquement)")

print("-" * 64)
print(f"RÉSULTAT : {_S['pass']} ok / {_S['fail']} ko")
sys.exit(1 if _S["fail"] else 0)
