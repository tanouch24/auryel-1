"""
test_google_play_verifier.py — B4 : vérificateur Google Play
(_google_verify_subscription / _google_normalize / _google_entitled /
_google_acknowledge_subscription) + route /api/billing/verify côté Google.

AUCUN réseau : _google_api_request et _google_access_token sont mockés.
Fixtures = formes officielles de purchases.subscriptionsv2.get
(SubscriptionPurchaseV2) et orders.get (Order), champs vérifiés sur la doc
Google Play Developer API v3.

Point critique testé : current_period_start vient du createTime de l'Order du
cycle COURANT (latestSuccessfulOrderId) et AVANCE à chaque renouvellement —
jamais expiryTime - 30 jours.

Script (pas pytest).
"""

import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from unittest.mock import MagicMock
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

import requests as _requests
import auryel_bot as A

_S = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _S["pass"] += 1
        print(f"✅ {label}")
    else:
        _S["fail"] += 1
        print(f"❌ {label}")


NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
PID = "auryel_premium_monthly"
TOK = "purchase-token-XYZ"

# ---------------------------------------------------------------------------
# Fixtures officielles
# ---------------------------------------------------------------------------


def gp_sub(state="SUBSCRIPTION_STATE_ACTIVE", product_id=PID,
           expiry="2026-09-28T12:00:00Z", start="2026-06-28T12:00:00Z",
           auto_renew=True, ack="ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED",
           latest_order="GPA.3300-0000-0000-00003", line_order=None,
           drop_expiry=False, drop_line_order=False):
    li = {
        "productId": product_id,
        "autoRenewingPlan": {"autoRenewEnabled": auto_renew},
        "offerDetails": {"basePlanId": "monthly", "offerId": None, "offerTags": []},
    }
    if not drop_expiry:
        li["expiryTime"] = expiry
    if not drop_line_order:
        li["latestSuccessfulOrderId"] = line_order or latest_order
    return {
        "kind": "androidpublisher#subscriptionPurchaseV2",
        "regionCode": "FR",
        "startTime": start,
        "subscriptionState": state,
        "latestOrderId": latest_order,
        "acknowledgementState": ack,
        "testPurchase": {},
        "lineItems": [li],
    }


def gp_order(order_id="GPA.3300-0000-0000-00003",
             create_time="2026-08-28T00:00:00Z", state="PROCESSED"):
    return {"orderId": order_id, "purchaseToken": TOK, "state": state,
            "createTime": create_time, "lastEventTime": create_time,
            "lineItems": [], "total": {"currencyCode": "EUR", "units": "7", "nanos": 990000000}}


def make_api(sub_ret=(200, None), order_ret=(200, None), raise_on=None):
    """Retourne une fonction de remplacement de _google_api_request qui
    répond selon le segment de path (subscriptionsv2 / orders)."""
    def _fake(method, path, body=None):
        if raise_on and raise_on in path:
            raise _requests.exceptions.Timeout("boom")
        if "/subscriptionsv2/" in path:
            return sub_ret
        if "/orders/" in path:
            return order_ret
        if ":acknowledge" in path:
            return (200, None)
        raise AssertionError("path inattendu : " + path)
    return _fake


def verify(sub_ret=(200, None), order_ret=(200, None), raise_on=None, now=NOW,
           token=TOK, product_id=PID):
    with patch.object(A, "_google_api_request",
                      side_effect=make_api(sub_ret, order_ret, raise_on)):
        return A._google_verify_subscription(token, product_id, now=now)


print("=" * 64)
print("TEST GOOGLE PLAY VERIFIER — B4")
print("=" * 64)

# --- 1. ACTIVE ------------------------------------------------------------
n = verify(sub_ret=(200, gp_sub(state="SUBSCRIPTION_STATE_ACTIVE")),
           order_ret=(200, gp_order()))
check(n["entitled"] is True and n["status"] == "SUBSCRIPTION_STATE_ACTIVE",
      "1 ACTIVE + expiry future -> entitled True")
check(n["store"] == "google_play" and n["product_id"] == PID
      and n["subscription_key"] == TOK
      and n["latest_transaction_id"] == "GPA.3300-0000-0000-00003"
      and n["auto_renewing"] is True,
      "1b champs normalisés : store/product_id/subscription_key(=token)/latest_transaction_id/auto_renewing")
check(n["purchased_at"] == datetime(2026, 6, 28, 12, tzinfo=timezone.utc),
      "1c purchased_at = startTime de l'abonnement (premier achat)")
check(n["expires_at"] == datetime(2026, 9, 28, 12, tzinfo=timezone.utc),
      "1d expires_at = lineItems[].expiryTime")
check(n["current_period_start"] == datetime(2026, 8, 28, 0, tzinfo=timezone.utc),
      "1e current_period_start = createTime de l'Order courant (PAS expiry - 30j)")
check(n["raw_payload"].get("subscription_state") == "SUBSCRIPTION_STATE_ACTIVE"
      and set(n["raw_payload"].keys()) == {"source", "subscription_state",
          "acknowledgement_state", "test_purchase", "region_code"},
      "1f raw_payload filtré : uniquement des champs d'état non sensibles")
check(TOK not in str(n["raw_payload"]), "1g raw_payload ne contient PAS le purchase_token")

# --- 2. CANCELED mais période encore valide ---------------------------
n = verify(sub_ret=(200, gp_sub(state="SUBSCRIPTION_STATE_CANCELED",
                                expiry="2026-09-28T12:00:00Z")),
           order_ret=(200, gp_order()))
check(n["entitled"] is True and n["status"] == "SUBSCRIPTION_STATE_CANCELED",
      "2 CANCELED + expiry future -> entitled True (payé jusqu'à la fin)")

# --- 3. IN_GRACE_PERIOD ---------------------------------------------
n = verify(sub_ret=(200, gp_sub(state="SUBSCRIPTION_STATE_IN_GRACE_PERIOD")),
           order_ret=(200, gp_order()))
check(n["entitled"] is True, "3 IN_GRACE_PERIOD -> entitled True (accès maintenu)")

# --- 4. ON_HOLD ---------------------------------------------------
n = verify(sub_ret=(200, gp_sub(state="SUBSCRIPTION_STATE_ON_HOLD")),
           order_ret=(200, gp_order()))
check(n["entitled"] is False, "4 ON_HOLD -> entitled False (accès suspendu)")

# --- 5. PAUSED --------------------------------------------------
n = verify(sub_ret=(200, gp_sub(state="SUBSCRIPTION_STATE_PAUSED")),
           order_ret=(200, gp_order()))
check(n["entitled"] is False, "5 PAUSED -> entitled False")

# --- 6. EXPIRED -----------------------------------------------
n = verify(sub_ret=(200, gp_sub(state="SUBSCRIPTION_STATE_EXPIRED",
                                expiry="2026-08-01T12:00:00Z")),
           order_ret=(200, gp_order()))
check(n["entitled"] is False, "6 EXPIRED -> entitled False")

# --- 7. ACTIVE mais expiry déjà passée (donnée périmée / révocation) --
n = verify(sub_ret=(200, gp_sub(state="SUBSCRIPTION_STATE_ACTIVE",
                                expiry="2026-08-01T00:00:00Z")),
           order_ret=(200, gp_order()))
check(n["entitled"] is False,
      "7 état ACTIVE mais expiryTime <= now -> entitled False (jamais entitled sur expiry seule ni l'inverse)")

# --- 8. mauvais productId -> product_mismatch -----------------
err = None
try:
    verify(sub_ret=(200, gp_sub(product_id="com.autre.produit")),
           order_ret=(200, gp_order()))
except A.StoreVerificationError as e:
    err = e
check(err is not None and err.code == "product_mismatch" and err.retryable is False,
      "8 productId != attendu -> product_mismatch (non retryable)")

# --- 9. token inconnu / 404 -> invalid_store_receipt --------
err = None
try:
    verify(sub_ret=(404, {"error": {"code": 404, "message": "not found"}}))
except A.StoreVerificationError as e:
    err = e
check(err is not None and err.code == "invalid_store_receipt" and err.retryable is False,
      "9 subscriptionsv2 404 -> invalid_store_receipt")

# --- 10. 401/403 -> store_verification_unavailable ----------
for code in (401, 403):
    err = None
    try:
        verify(sub_ret=(code, {"error": {"message": "denied"}}))
    except A.StoreVerificationError as e:
        err = e
    check(err is not None and err.code == "store_verification_unavailable"
          and err.retryable is True,
          f"10 subscriptionsv2 {code} -> store_verification_unavailable (retryable)")

# --- 11. 429 -> store_verification_unavailable -------------
err = None
try:
    verify(sub_ret=(429, {"error": {"message": "rate"}}))
except A.StoreVerificationError as e:
    err = e
check(err is not None and err.code == "store_verification_unavailable",
      "11 subscriptionsv2 429 -> store_verification_unavailable")

# --- 12. 500 Google -> store_verification_unavailable -----
err = None
try:
    verify(sub_ret=(500, {"error": {"message": "oops"}}))
except A.StoreVerificationError as e:
    err = e
check(err is not None and err.code == "store_verification_unavailable",
      "12 subscriptionsv2 500 -> store_verification_unavailable")

# --- 13. timeout -> store_verification_unavailable --------
# (_google_api_request lève requests.RequestException -> capté dedans, mais ici
#  on teste que le vérificateur propage bien une StoreVerificationError si
#  _google_api_request lève. On force via une vraie _google_api_request patchée
#  qui lève RequestException et on vérifie le mapping du helper.)
with patch.object(A, "_google_access_token", return_value="fake-token"), \
     patch.object(A.requests, "request",
                  side_effect=_requests.exceptions.ConnectTimeout("nope")):
    err = None
    try:
        A._google_verify_subscription(TOK, PID, now=NOW)
    except A.StoreVerificationError as e:
        err = e
check(err is not None and err.code == "store_verification_unavailable"
      and err.retryable is True,
      "13 timeout réseau -> store_verification_unavailable (retryable)")

# --- 14. acknowledgement pending -> POST :acknowledge ------
calls = []


def _ack_api(method, path, body=None):
    calls.append((method, path))
    if ":acknowledge" in path:
        return (204, None)
    raise AssertionError(path)


with patch.object(A, "_google_api_request", side_effect=_ack_api):
    res = A._google_acknowledge_subscription(TOK, PID,
                                             ack_state="ACKNOWLEDGEMENT_STATE_PENDING")
check(res.get("acknowledged") is True and any(":acknowledge" in p for _, p in calls)
      and calls[0][0] == "POST",
      "14 ack PENDING -> POST ...:acknowledge, 204 -> acknowledged True")

# --- 15. déjà acknowledged -> no-op, aucun appel réseau ---
calls.clear()
with patch.object(A, "_google_api_request", side_effect=_ack_api):
    res = A._google_acknowledge_subscription(
        TOK, PID, ack_state="ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED")
check(res.get("acknowledged") is False and calls == [],
      "15 ack_state=ACKNOWLEDGED -> no-op, 0 appel réseau")

# variante : Google renvoie 400 "already acknowledged" -> traité no-op
with patch.object(A, "_google_api_request",
                  side_effect=lambda m, p, body=None: (400, {"error": {"message":
                      "The subscription purchase is already acknowledged."}})):
    res = A._google_acknowledge_subscription(TOK, PID,
                                             ack_state="ACKNOWLEDGEMENT_STATE_PENDING")
check(res.get("acknowledged") is False and res.get("reason") == "already_acknowledged",
      "15b 400 'already acknowledged' -> no-op (idempotent)")

# --- 16. ack échoue -> StoreVerificationError retryable, puis retry OK --
with patch.object(A, "_google_api_request",
                  side_effect=lambda m, p, body=None: (503, {"error": {"message": "down"}})):
    err = None
    try:
        A._google_acknowledge_subscription(TOK, PID,
                                           ack_state="ACKNOWLEDGEMENT_STATE_PENDING")
    except A.StoreVerificationError as e:
        err = e
check(err is not None and err.code == "store_verification_unavailable"
      and err.retryable is True,
      "16 ack 503 -> store_verification_unavailable (retryable)")
with patch.object(A, "_google_api_request",
                  side_effect=lambda m, p, body=None: (200, None)):
    res = A._google_acknowledge_subscription(TOK, PID,
                                             ack_state="ACKNOWLEDGEMENT_STATE_PENDING")
check(res.get("acknowledged") is True, "16b retry acknowledge -> OK (200)")

# --- 17. current_period_start = cycle N -----------------
n = verify(sub_ret=(200, gp_sub(latest_order="GPA.N",
                                expiry="2026-09-28T12:00:00Z")),
           order_ret=(200, gp_order(order_id="GPA.N",
                                    create_time="2026-08-28T00:00:00Z")))
check(n["current_period_start"] == datetime(2026, 8, 28, 0, tzinfo=timezone.utc),
      "17 cycle N : current_period_start = createTime de l'Order N")

# --- 18. renouvellement cycle N+1 -> current_period_start AVANCE --
n2 = verify(sub_ret=(200, gp_sub(latest_order="GPA.N1",
                                 expiry="2026-10-28T12:00:00Z",
                                 start="2026-06-28T12:00:00Z")),
            order_ret=(200, gp_order(order_id="GPA.N1",
                                     create_time="2026-09-28T00:05:00Z")),
            now=datetime(2026, 9, 28, 12, tzinfo=timezone.utc))
check(n2["current_period_start"] == datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc),
      "18 cycle N+1 : current_period_start AVANCE (createTime du nouvel Order)")
check(n2["purchased_at"] == datetime(2026, 6, 28, 12, tzinfo=timezone.utc),
      "18b purchased_at (startTime) INCHANGÉ après renouvellement")
check(n2["expires_at"] == datetime(2026, 10, 28, 12, tzinfo=timezone.utc),
      "18c expires_at AVANCE au nouveau cycle")

# --- 19. replay même token -> structure normalisée identique ---
a = verify(sub_ret=(200, gp_sub()), order_ret=(200, gp_order()))
b = verify(sub_ret=(200, gp_sub()), order_ret=(200, gp_order()))
check(a == b, "19 replay même purchase_token -> structure normalisée identique (idempotence en amont de record)")

# --- 20. lineItem sans latestSuccessfulOrderId -> invalid_store_receipt ----
# SubscriptionPurchaseLineItem V2 n'expose QUE latestSuccessfulOrderId ; aucun
# fallback latestOrderId (champ non documenté). Une fixture ne portant qu'un
# latestOrderId fictif ne doit JAMAIS être acceptée.
err = None
try:
    verify(sub_ret=(200, gp_sub(drop_line_order=True, latest_order="GPA.FICTIF")),
           order_ret=(200, gp_order(order_id="GPA.FICTIF",
                                    create_time="2026-08-28T00:00:00Z")))
except A.StoreVerificationError as e:
    err = e
check(err is not None and err.code == "invalid_store_receipt" and err.retryable is False,
      "20 pas de latestSuccessfulOrderId (seul latestOrderId fictif) -> invalid_store_receipt")

# --- 20b. latestSuccessfulOrderId présent -> OK, latestOrderId de l'abo ignoré
n = verify(sub_ret=(200, gp_sub(line_order="GPA.LINE-OK", latest_order="GPA.IGNORE")),
           order_ret=(200, gp_order(order_id="GPA.LINE-OK",
                                    create_time="2026-08-28T00:00:00Z")))
check(n["current_period_start"] == datetime(2026, 8, 28, 0, tzinfo=timezone.utc)
      and n["latest_transaction_id"] == "GPA.LINE-OK",
      "20b latestSuccessfulOrderId présent -> current_period_start résolu ; "
      "latest_transaction_id = latestSuccessfulOrderId (latestOrderId ignoré)")

# --- 21. expiryTime absent -> invalid_store_receipt ---
err = None
try:
    verify(sub_ret=(200, gp_sub(drop_expiry=True)), order_ret=(200, gp_order()))
except A.StoreVerificationError as e:
    err = e
check(err is not None and err.code == "invalid_store_receipt",
      "21 expiryTime absent -> invalid_store_receipt (jamais inventé)")

# --- 22. Order sans createTime -> invalid_store_receipt ---
err = None
try:
    verify(sub_ret=(200, gp_sub()), order_ret=(200, {"orderId": "GPA.X", "state": "PROCESSED"}))
except A.StoreVerificationError as e:
    err = e
check(err is not None and err.code == "invalid_store_receipt",
      "22 Order sans createTime -> invalid_store_receipt (pas de current_period_start fabriqué)")

# --- 23. Order 404 -> store_verification_unavailable ---
err = None
try:
    verify(sub_ret=(200, gp_sub()), order_ret=(404, {"error": {"message": "no order"}}))
except A.StoreVerificationError as e:
    err = e
check(err is not None and err.code == "invalid_store_receipt",
      "23 orders.get 404 -> invalid_store_receipt")

# --- 24. _google_entitled : table de vérité isolée ---
fut = NOW + timedelta(days=10)
past = NOW - timedelta(days=1)
check(A._google_entitled("SUBSCRIPTION_STATE_ACTIVE", fut, NOW) is True
      and A._google_entitled("SUBSCRIPTION_STATE_ACTIVE", past, NOW) is False
      and A._google_entitled("SUBSCRIPTION_STATE_CANCELED", fut, NOW) is True
      and A._google_entitled("SUBSCRIPTION_STATE_IN_GRACE_PERIOD", fut, NOW) is True
      and A._google_entitled("SUBSCRIPTION_STATE_ON_HOLD", fut, NOW) is False
      and A._google_entitled("SUBSCRIPTION_STATE_PAUSED", fut, NOW) is False
      and A._google_entitled("SUBSCRIPTION_STATE_EXPIRED", fut, NOW) is False
      and A._google_entitled("SUBSCRIPTION_STATE_PENDING", fut, NOW) is False
      and A._google_entitled("", fut, NOW) is False
      and A._google_entitled("SUBSCRIPTION_STATE_ACTIVE", None, NOW) is False,
      "24 _google_entitled : ACTIVE/CANCELED/GRACE=True(si futur), ON_HOLD/PAUSED/EXPIRED/PENDING/inconnu=False")

# --- 25. route /api/billing/verify côté Google (vérificateur RÉEL, HTTP mocké) --
A.app.config["TESTING"] = True
A.limiter.enabled = False
_UID = "11111111-1111-4111-8111-111111111111"
A.resolve_app_session = lambda tok: ({"session_id": "s", "user_id": _UID,
                                      "email": "u@x"} if tok == "T" else None)
_rec, _res, _ack = [], [], []
A.record_mobile_subscription = lambda **kw: _rec.append(kw) or {
    "outcome": "created", "id": "s1", "store": kw["store"], "status": kw["status"]}
A.resync_premium_entitlement = lambda uid, now=None: _res.append(uid)
A.get_consultation_state = lambda uid, now=None: {"consultation": None, "quota": {
    "is_premium": True, "monthly_limit": 4, "monthly_used": 0, "monthly_remaining": 4,
    "earned_available": 0, "first_free_available": True, "period_start": NOW,
    "period_end": NOW + timedelta(days=30)}}
A._google_acknowledge_subscription = lambda t, p, ack_state=None: _ack.append((t, p, ack_state))
cli = A.app.test_client()

with patch.object(A, "_google_api_request",
                  side_effect=make_api((200, gp_sub(ack="ACKNOWLEDGEMENT_STATE_PENDING")),
                                       (200, gp_order()))):
    r = cli.post("/api/billing/verify",
                 json={"store": "google_play", "product_id": PID, "purchase_token": TOK},
                 headers={"Authorization": "Bearer T"})
j = r.get_json()
check(r.status_code == 200 and j["subscription"]["entitled"] is True
      and j["subscription"]["store"] == "google_play",
      "25 route Google bout-en-bout (HTTP mocké) -> 200, entitled True")
check(len(_rec) == 1 and _rec[0]["subscription_key"] == TOK
      and _rec[0]["current_period_start"] == datetime(2026, 8, 28, 0, tzinfo=timezone.utc)
      and isinstance(_rec[0]["entitled"], bool),
      "25b record reçoit la struct normalisée (subscription_key=token, cps du store, entitled bool)")
check(_res == [_UID] and _ack == [(TOK, PID, "ACKNOWLEDGEMENT_STATE_PENDING")],
      "25c resync puis acknowledge (PENDING) appelés dans l'ordre, après record")
check(TOK not in r.get_data(as_text=True),
      "25d réponse route ne contient pas le purchase_token")

print("-" * 64)
print(f"RÉSULTAT : {_S['pass']} ok / {_S['fail']} ko")
sys.exit(1 if _S["fail"] else 0)
