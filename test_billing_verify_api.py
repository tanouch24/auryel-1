"""
test_billing_verify_api.py — B4 : POST /api/billing/verify.

Périmètre : contrat de la ROUTE (auth, validation du body, identité issue du
Bearer uniquement, dispatch store, mapping des erreurs, forme de la réponse,
Cache-Control). Les vérificateurs store sont MOCKÉS à la frontière de fonction
(_google_verify_subscription / _apple_verify_subscription) — AUCUN réseau.

record_mobile_subscription / resync_premium_entitlement / get_consultation_state
sont remplacés par un modèle mémoire minimal : on observe l'idempotence, la
préservation de monthly_used et la forme du quota sans rejouer tout le SQL
(couvert par test_billing_record_subscription / test_billing_sync_allowance /
test_billing_verify_e2e).

Script (pas pytest) : sys.exit(1) si un test échoue.
"""

import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

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
        print(f"✅ {label}")
    else:
        _STATE["fail"] += 1
        print(f"❌ {label}")


A.app.config["TESTING"] = True
A.app.config["RATELIMIT_ENABLED"] = False
A.limiter.enabled = False
client = A.app.test_client()

UID = "11111111-1111-4111-8111-111111111111"
UID_OTHER = "99999999-9999-4999-8999-999999999999"
GOOD_TOKEN = "good-bearer-token"
NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)

# --- identité : Bearer -> compte, sans DB ------------------------------------
A.resolve_app_session = lambda tok: (
    {"session_id": "sess-1", "user_id": UID, "email": "u@example.com"}
    if tok == GOOD_TOKEN else None
)

# --- modèle mémoire pour record / resync / state ----------------------------
MEM = {"subs": {}, "resync": [], "ack": [], "raise_resync": False, "raise_state": False}


def _reset_mem():
    MEM["subs"].clear()
    MEM["resync"].clear()
    MEM["ack"].clear()
    MEM["raise_resync"] = False
    MEM["raise_state"] = False


def fake_record(**kw):
    key = (kw["store"], kw["subscription_key"])
    row = MEM["subs"].get(key)
    if row is not None and row["user_id"] != str(kw["user_id"]):
        raise A.MobileSubscriptionError("account_mismatch", "déjà rattaché")
    if not isinstance(kw["entitled"], bool):
        raise A.MobileSubscriptionError("missing_field", "entitled non booléen")
    if kw["current_period_start"] is None:
        raise A.MobileSubscriptionError("missing_field", "cps requis")
    if row is None:
        MEM["subs"][key] = {
            "user_id": str(kw["user_id"]), "entitled": kw["entitled"],
            "status": kw["status"], "expires_at": kw["expires_at"],
            "current_period_start": kw["current_period_start"],
            "monthly_used": 0, "writes": 1,
        }
        return {"outcome": "created", "id": "sub-" + kw["subscription_key"],
                "store": kw["store"], "status": kw["status"]}
    row["entitled"] = kw["entitled"]
    row["status"] = kw["status"]
    row["expires_at"] = kw["expires_at"]
    row["current_period_start"] = kw["current_period_start"]
    row["writes"] += 1
    return {"outcome": "updated", "id": "sub-" + kw["subscription_key"],
            "store": kw["store"], "status": kw["status"]}


def fake_resync(user_id, now=None):
    MEM["resync"].append(str(user_id))
    if MEM["raise_resync"]:
        raise RuntimeError("resync boom")
    return {"premium": True, "action": "provisioned", "period_start": NOW,
            "period_end": NOW + timedelta(days=30), "anomaly": None}


def fake_state(user_id, now=None):
    if MEM["raise_state"]:
        raise RuntimeError("state boom")
    entitled_rows = [r for r in MEM["subs"].values()
                     if r["user_id"] == str(user_id) and r["entitled"]]
    if entitled_rows:
        used = max(r["monthly_used"] for r in entitled_rows)
        quota = {"is_premium": True, "monthly_limit": 4, "monthly_used": used,
                 "monthly_remaining": max(0, 4 - used), "earned_available": 0,
                 "first_free_available": True, "period_start": NOW,
                 "period_end": NOW + timedelta(days=30)}
    else:
        quota = {"is_premium": False, "monthly_limit": 0, "monthly_used": 0,
                 "monthly_remaining": 0, "earned_available": 0,
                 "first_free_available": True, "period_start": None, "period_end": None}
    return {"consultation": None, "quota": quota}


def fake_ack(purchase_token, product_id, ack_state=None):
    MEM["ack"].append((purchase_token, product_id, ack_state))
    return {"acknowledged": True}


def fake_record_and_resync(**kw):
    """Compose fake_record + fake_resync avec la sémantique ATOMIQUE réelle :
    si le resync échoue APRÈS l'INSERT/UPDATE de la subscription, la mutation
    du record est ANNULÉE (rollback global) — aucune demi-écriture."""
    import copy
    user_id = kw["user_id"]
    subs_before = copy.deepcopy(MEM["subs"])
    resync_len_before = len(MEM["resync"])
    rec = fake_record(**kw)                       # MobileSubscriptionError = pré-écriture
    try:
        res = fake_resync(user_id, now=kw.get("now"))
    except Exception:
        MEM["subs"].clear()
        MEM["subs"].update(subs_before)           # la ligne record disparaît
        del MEM["resync"][resync_len_before:]     # aucune trace resync
        raise
    return {"subscription": rec, "resync": res}


A.record_mobile_subscription = fake_record
A.resync_premium_entitlement = fake_resync
A.record_and_resync_mobile_subscription = fake_record_and_resync
A.get_consultation_state = fake_state
A._google_acknowledge_subscription = fake_ack

# --- structure normalisée type (ce que renverrait un vérificateur store) -----


def _norm(store="google_play", entitled=True, product_id="auryel_premium_monthly",
          key="orig-tx-1", status="SUBSCRIPTION_STATE_ACTIVE"):
    return {
        "store": store, "product_id": product_id, "subscription_key": key,
        "latest_transaction_id": "order-123", "status": status,
        "entitled": entitled, "purchased_at": NOW - timedelta(days=1),
        "current_period_start": NOW, "expires_at": NOW + timedelta(days=30),
        "auto_renewing": True,
        "raw_payload": {"source": store, "acknowledgement_state":
                        "ACKNOWLEDGEMENT_STATE_PENDING", "secret_should_not_leak": "x"},
    }


def _post(body, token=GOOD_TOKEN, raw=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if raw is not None:
        return client.post("/api/billing/verify", data=raw,
                           content_type="application/json", headers=headers)
    return client.post("/api/billing/verify", json=body, headers=headers)


G_BODY = {"store": "google_play", "product_id": "auryel_premium_monthly",
          "purchase_token": "tok-AAA"}
A_BODY = {"store": "app_store", "product_id": "auryel_premium_monthly",
          "transaction_id": "1000000999"}

print("=" * 64)
print("TEST BILLING VERIFY API — POST /api/billing/verify")
print("=" * 64)

# --- 1. sans Bearer -> 401 --------------------------------------------------
_reset_mem()
r = _post(G_BODY, token=None)
check(r.status_code == 401 and r.get_json().get("error") == "unauthorized",
      "1 sans Bearer -> 401 unauthorized")
check("no-store" in r.headers.get("Cache-Control", ""), "1b 401 -> Cache-Control: no-store")

# --- 2. JSON invalide -> 400 invalid_request ------------------------------
r = _post(None, raw="{ not json")
check(r.status_code == 400 and r.get_json().get("error") == "invalid_request",
      "2 body non JSON -> 400 invalid_request")
r = _post(None, raw='"a string not an object"')
check(r.status_code == 400 and r.get_json().get("error") == "invalid_request",
      "2b body JSON non-objet -> 400 invalid_request")
r = _post(None, raw="")
check(r.status_code == 400 and r.get_json().get("error") == "invalid_request",
      "2c body vide -> 400 invalid_request")

# --- 3. store invalide ----------------------------------------------------
for bad in ("stripe", "PlayStore", "", "GOOGLE_PLAY"):
    r = _post({**G_BODY, "store": bad})
    check(r.status_code == 400 and r.get_json().get("error") == "invalid_store",
          f"3 store={bad!r} -> 400 invalid_store")
r = _post({"product_id": "auryel_premium_monthly", "purchase_token": "x"})
check(r.status_code == 400 and r.get_json().get("error") == "invalid_store",
      "3b store absent -> 400 invalid_store")

# --- 4. product_id invalide ---------------------------------------------
for bad in ("auryel_consultation_extra", "premium", ""):
    r = _post({**G_BODY, "product_id": bad})
    check(r.status_code == 400 and r.get_json().get("error") == "invalid_product",
          f"4 product_id={bad!r} -> 400 invalid_product")

# --- 5. Google sans purchase_token ------------------------------------
for body in ({"store": "google_play", "product_id": "auryel_premium_monthly"},
             {**G_BODY, "purchase_token": ""},
             {**G_BODY, "purchase_token": "   "},
             {**G_BODY, "purchase_token": 123}):
    r = _post(body)
    check(r.status_code == 400 and r.get_json().get("error") == "missing_purchase_token",
          f"5 google purchase_token manquant/invalide ({body.get('purchase_token')!r}) -> 400 missing_purchase_token")

# --- 6. Apple sans transaction_id ----------------------------------
for body in ({"store": "app_store", "product_id": "auryel_premium_monthly"},
             {**A_BODY, "transaction_id": ""},
             {**A_BODY, "transaction_id": None}):
    r = _post(body)
    check(r.status_code == 400 and r.get_json().get("error") == "missing_transaction_id",
          f"6 apple transaction_id manquant ({body.get('transaction_id')!r}) -> 400 missing_transaction_id")

# --- 7-9. body ne peut jamais dicter identité / droits ---------------
_reset_mem()
with patch.object(A, "_google_verify_subscription", return_value=_norm()) as mv:
    r = _post({**G_BODY, "user_id": UID_OTHER, "email": "attacker@x.com",
               "entitled": False, "status": "SUBSCRIPTION_STATE_EXPIRED",
               "expires_at": "1999-01-01T00:00:00Z",
               "current_period_start": "1999-01-01T00:00:00Z",
               "subscription_key": "attacker-key"})
j = r.get_json()
check(r.status_code == 200, "7 champs parasites dans le body -> 200 (ignorés)")
check(list(MEM["subs"].values())[0]["user_id"] == UID,
      "7b sub rattaché au user_id du Bearer, pas à body.user_id")
check(mv.call_args[0][0] == "tok-AAA",
      "7c vérificateur appelé avec le purchase_token du body, pas subscription_key parasite")
check(j["subscription"]["entitled"] is True and j["quota"]["is_premium"] is True,
      "8 entitled=false dans le body -> ignoré, entitlement = décision store (true)")
_sub = list(MEM["subs"].values())[0]
check(_sub["current_period_start"] == NOW and _sub["expires_at"] == NOW + timedelta(days=30),
      "9 dates falsifiées dans le body -> ignorées, dates = celles du store")

# --- 10. struct active -> record + resync + quota Premium -------------
_reset_mem()
with patch.object(A, "_google_verify_subscription", return_value=_norm()):
    r = _post(G_BODY)
j = r.get_json()
check(r.status_code == 200, "10a struct active -> 200")
check(len(MEM["subs"]) == 1 and list(MEM["subs"].values())[0]["writes"] == 1,
      "10b record_mobile_subscription appelé une fois (1 ligne)")
check(MEM["resync"] == [UID], "10c resync_premium_entitlement appelé avec le user du Bearer")
check(j["quota"]["is_premium"] is True and j["quota"]["monthly_remaining"] == 4,
      "10d quota renvoyé : Premium actif")
check(j["subscription"]["store"] == "google_play"
      and j["subscription"]["product_id"] == "auryel_premium_monthly"
      and j["subscription"]["status"] == "SUBSCRIPTION_STATE_ACTIVE"
      and j["subscription"]["entitled"] is True
      and j["subscription"]["expires_at"] == (NOW + timedelta(days=30)).isoformat(),
      "10e bloc subscription complet et cohérent")
check("no-store" in r.headers.get("Cache-Control", ""), "10f 200 -> Cache-Control: no-store")

# --- 11. replay -> idempotent --------------------------------------
list(MEM["subs"].values())[0]["monthly_used"] = 2       # conso simulée entre deux verify
with patch.object(A, "_google_verify_subscription", return_value=_norm()):
    r2 = _post(G_BODY)
j2 = r2.get_json()
check(r2.status_code == 200 and len(MEM["subs"]) == 1,
      "11a même verify rejoué -> toujours 1 ligne")
check(list(MEM["subs"].values())[0]["writes"] == 2
      and list(MEM["subs"].values())[0]["monthly_used"] == 2,
      "11b 2e passage = UPDATE, monthly_used préservé (2)")
check(MEM["resync"] == [UID, UID], "11c resync rejoué (idempotent côté projection)")
check(j2["quota"]["monthly_used"] == 2 and j2["quota"]["monthly_remaining"] == 2,
      "11d quota reflète monthly_used=2 après replay")

# --- 12. account_mismatch -> 409 ---------------------------------
_reset_mem()
MEM["subs"][("google_play", "orig-tx-1")] = {
    "user_id": UID_OTHER, "entitled": True, "status": "x",
    "expires_at": NOW, "current_period_start": NOW, "monthly_used": 0, "writes": 1}
with patch.object(A, "_google_verify_subscription", return_value=_norm(key="orig-tx-1")):
    r = _post(G_BODY)
check(r.status_code == 409 and r.get_json().get("error") == "account_mismatch",
      "12 achat déjà rattaché à un autre compte -> 409 account_mismatch")
check(MEM["resync"] == [], "12b resync PAS appelé après account_mismatch")

# --- 13. reçu invalide -> 422 ----------------------------------
_reset_mem()
with patch.object(A, "_google_verify_subscription",
                  side_effect=A.StoreVerificationError("invalid_store_receipt", "x")):
    r = _post(G_BODY)
check(r.status_code == 422 and r.get_json().get("error") == "invalid_store_receipt",
      "13 StoreVerificationError invalid_store_receipt -> 422")
check(MEM["subs"] == {} and MEM["resync"] == [],
      "13b aucune écriture / aucun resync quand la vérif store échoue")
with patch.object(A, "_apple_verify_subscription",
                  side_effect=A.StoreVerificationError("product_mismatch", "x")):
    r = _post(A_BODY)
check(r.status_code == 422 and r.get_json().get("error") == "product_mismatch",
      "13c product_mismatch -> 422")

# --- 14. store indisponible / timeout -> 503 -----------------
with patch.object(A, "_google_verify_subscription",
                  side_effect=A.StoreVerificationError("store_verification_unavailable", "timeout")):
    r = _post(G_BODY)
check(r.status_code == 503 and r.get_json().get("error") == "store_verification_unavailable",
      "14 store injoignable/timeout -> 503 store_verification_unavailable")
with patch.object(A, "_apple_verify_subscription",
                  side_effect=A.StoreVerificationError("verification_not_configured", "no certs")):
    r = _post(A_BODY)
check(r.status_code == 503 and r.get_json().get("error") == "verification_not_configured",
      "14b verification_not_configured -> 503")

# --- 15. erreur interne resync -> 500 -----------------------
_reset_mem()
MEM["raise_resync"] = True
with patch.object(A, "_google_verify_subscription", return_value=_norm()):
    r = _post(G_BODY)
check(r.status_code == 500 and r.get_json().get("error") == "internal_error",
      "15 exception dans resync -> 500 internal_error")
check(len(MEM["subs"]) == 0 and MEM["resync"] == [],
      "15b ROLLBACK GLOBAL : échec resync -> la subscription n'est PAS committée "
      "(retry client rejoue record+resync, idempotent)")
check("no-store" in r.headers.get("Cache-Control", ""), "15c 500 -> Cache-Control: no-store")

_reset_mem()
MEM["raise_state"] = True
with patch.object(A, "_google_verify_subscription", return_value=_norm()):
    r = _post(G_BODY)
check(r.status_code == 500 and r.get_json().get("error") == "internal_error",
      "15d exception dans get_consultation_state -> 500 internal_error")

# --- 16. la réponse ne divulgue aucun secret / token / raw_payload -----
_reset_mem()
with patch.object(A, "_google_verify_subscription",
                  return_value=_norm(key="SECRET-ORIGINAL-TX")):
    r = _post({**G_BODY, "purchase_token": "SECRET-PURCHASE-TOKEN"})
body_txt = r.get_data(as_text=True)
check(r.status_code == 200, "16a struct active -> 200")
check("SECRET-PURCHASE-TOKEN" not in body_txt
      and "SECRET-ORIGINAL-TX" not in body_txt
      and "raw_payload" not in body_txt
      and "secret_should_not_leak" not in body_txt
      and "subscription_key" not in body_txt
      and "latest_transaction_id" not in body_txt,
      "16b réponse sans purchase_token / subscription_key / latest_transaction_id / raw_payload")
check(set(r.get_json()["subscription"].keys())
      == {"store", "product_id", "status", "entitled", "expires_at"},
      "16c bloc subscription = uniquement {store, product_id, status, entitled, expires_at}")

# --- 17. quota = même forme que /api/consultation/state --------------
_reset_mem()
_expected_quota_keys = set(A._quota_json(fake_state(UID)["quota"]).keys())
with patch.object(A, "_google_verify_subscription", return_value=_norm()):
    r = _post(G_BODY)
check(set(r.get_json()["quota"].keys()) == _expected_quota_keys
      and _expected_quota_keys == {"is_premium", "monthly_limit", "monthly_used",
                                   "monthly_remaining", "earned_available",
                                   "first_free_available", "period_start", "period_end"},
      "17 quota renvoyé strictement à la forme _quota_json (consultation state)")

# --- 18. Google acknowledge APRÈS attribution -----------------
_reset_mem()
with patch.object(A, "_google_verify_subscription", return_value=_norm()):
    r = _post(G_BODY)
check(r.status_code == 200 and MEM["ack"] == [("tok-AAA", "auryel_premium_monthly",
                                               "ACKNOWLEDGEMENT_STATE_PENDING")],
      "18 acknowledge Google appelé après record+resync, avec ack_state PENDING")

_reset_mem()
with patch.object(A, "_google_verify_subscription", return_value=_norm()):
    r = _post(A_BODY.copy()) if False else None
# Apple ne déclenche jamais l'acknowledge Google
with patch.object(A, "_apple_verify_subscription", return_value=_norm(store="app_store")):
    r = _post(A_BODY)
check(r.status_code == 200 and MEM["ack"] == [],
      "18b Apple -> aucun acknowledge Google")

# --- 19. échec acknowledge -> 503 (entitlement déjà accordé, retry safe) --
_reset_mem()
with patch.object(A, "_google_verify_subscription", return_value=_norm()), \
     patch.object(A, "_google_acknowledge_subscription",
                  side_effect=A.StoreVerificationError("store_verification_unavailable", "ack ko")):
    r = _post(G_BODY)
check(r.status_code == 503 and r.get_json().get("error") == "store_verification_unavailable",
      "19 échec acknowledge -> 503")
check(len(MEM["subs"]) == 1 and MEM["resync"] == [UID],
      "19b record + resync ont déjà eu lieu : le retry client ne double pas le quota")

# --- 20. Apple : chemin nominal complet -----------------------
_reset_mem()
with patch.object(A, "_apple_verify_subscription",
                  return_value=_norm(store="app_store", key="apple-orig-1",
                                     status="active")) as mva:
    r = _post({**A_BODY, "transaction_id": "txn-apple-42"})
j = r.get_json()
check(r.status_code == 200 and mva.call_args[0][0] == "txn-apple-42"
      and mva.call_args[0][1] == "auryel_premium_monthly",
      "20 Apple -> _apple_verify_subscription(transaction_id, product_id)")
check(j["subscription"]["store"] == "app_store" and j["quota"]["is_premium"] is True,
      "20b Apple -> subscription app_store, quota Premium")

# --- 21. entitled=false renvoyé par le store -> pas Premium ----------
_reset_mem()
with patch.object(A, "_google_verify_subscription",
                  return_value=_norm(entitled=False, status="SUBSCRIPTION_STATE_ON_HOLD")):
    r = _post(G_BODY)
j = r.get_json()
check(r.status_code == 200 and j["subscription"]["entitled"] is False
      and j["quota"]["is_premium"] is False,
      "21 store dit entitled=false (ON_HOLD) -> 200, Premium inactif, ligne quand même enregistrée")
check(len(MEM["subs"]) == 1 and MEM["resync"] == [UID],
      "21b record + resync tout de même exécutés (mobile_subscriptions = source de vérité)")

# --- 22. GET non autorisé sur la route -----------------------
r = client.get("/api/billing/verify", headers={"Authorization": f"Bearer {GOOD_TOKEN}"})
check(r.status_code == 405, "22 GET /api/billing/verify -> 405 (POST uniquement)")

print("-" * 64)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
