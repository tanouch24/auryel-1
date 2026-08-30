"""
test_billing_verify_e2e.py — B4 : POST /api/billing/verify de bout en bout.

Stores MOCKÉS à la frontière (_google_verify_subscription /
_apple_verify_subscription) ; TOUT le reste est réel : record_mobile_subscription
+ resync_premium_entitlement + get_consultation_state + open_or_get_consultation,
contre une fausse DB en mémoire (psycopg2 mocké).

Prouve : replay idempotent, jamais de quota 8 (multi-store), first_free
préservée, earned_credits intacts, monthly_used jamais remis à zéro hors
création légitime d'une nouvelle période, révocations/expirations.

Script (pas pytest).
"""

import sys
import uuid
from datetime import datetime, timezone, timedelta
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

import auryel_bot as A

_S = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _S["pass"] += 1
        print(f"✅ {label}")
    else:
        _S["fail"] += 1
        print(f"❌ {label}")


def _n(sql):
    return " ".join(sql.split())


def _distinct(a, b):
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    return a != b


DB = {"accounts": [], "mobile_subscriptions": [], "consultation_allowance": [],
      "earned_credits": [], "consultations": []}


def reset():
    for v in DB.values():
        v.clear()


# --- SQL record_mobile_subscription ----------------------------------------
MS_SEL = _n("SELECT id, user_id FROM mobile_subscriptions WHERE store=%s AND subscription_key=%s FOR UPDATE")
MS_INS = _n("INSERT INTO mobile_subscriptions (id, user_id, store, product_id, subscription_key, "
            "latest_transaction_id, status, entitled, purchased_at, current_period_start, expires_at, "
            "auto_renewing, last_verified_at, raw_payload, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (store, subscription_key) DO NOTHING")
MS_UPD = _n("UPDATE mobile_subscriptions SET product_id=%s, latest_transaction_id=%s, status=%s, "
            "entitled=%s, current_period_start=%s, expires_at=%s, auto_renewing=%s, "
            "last_verified_at=%s, raw_payload=%s, updated_at=%s WHERE id=%s")

# --- SQL resync_premium_entitlement --------------------------------------
R_ACC = _n("SELECT user_id FROM accounts WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE")
R_SUBS = _n("SELECT id, current_period_start, expires_at, entitled, store FROM mobile_subscriptions WHERE user_id=%s")
R_ALW = _n("SELECT period_start, period_end, monthly_limit, monthly_used, source_subscription_id, "
           "source_period_start FROM consultation_allowance WHERE user_id=%s FOR UPDATE")
R_UPE = _n("UPDATE consultation_allowance SET period_end=%s WHERE user_id=%s AND period_start=%s")
R_UPE_SRC = _n("UPDATE consultation_allowance SET period_end=%s, source_subscription_id=%s, "
               "source_period_start=%s WHERE user_id=%s AND period_start=%s")
R_DEL_ALL = _n("DELETE FROM consultation_allowance WHERE user_id=%s AND period_start > %s")
R_DEL_NC = _n("DELETE FROM consultation_allowance WHERE user_id=%s AND period_start > %s "
              "AND (source_subscription_id IS DISTINCT FROM %s OR source_period_start IS DISTINCT FROM %s)")
INS_ALW = _n("INSERT INTO consultation_allowance (user_id, period_start, period_end, monthly_limit, "
             "monthly_used, created_at, source_subscription_id, source_period_start) "
             "VALUES (%s, %s, %s, %s, 0, %s, %s, %s) ON CONFLICT (user_id, period_start) DO NOTHING")

# --- SQL get_consultation_state ---------------------------------------
GS_CONS = _n("SELECT id, advisor_id, started_at, expires_at, credit_source FROM consultations "
             "WHERE user_id=%s AND expires_at > %s ORDER BY started_at DESC LIMIT 1")
GS_ALW = _n("SELECT period_start, period_end, monthly_limit, monthly_used FROM consultation_allowance "
            "WHERE user_id=%s AND period_start <= %s AND period_end > %s ORDER BY period_start DESC LIMIT 1")
GS_EARNED = _n("SELECT COUNT(*) FROM earned_credits WHERE user_id=%s AND consumed_at IS NULL")
GS_FF = _n("SELECT first_consultation_used_at FROM accounts WHERE user_id=%s")

# --- SQL open_or_get_consultation ------------------------------------
OG_ACC = _n("SELECT user_id, first_consultation_used_at FROM accounts WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE")
OG_ALW = _n("SELECT period_start, monthly_limit, monthly_used FROM consultation_allowance "
            "WHERE user_id=%s AND period_start <= %s AND period_end > %s ORDER BY period_start DESC LIMIT 1 FOR UPDATE")
OG_UPD_USED = _n("UPDATE consultation_allowance SET monthly_used = monthly_used + 1 WHERE user_id=%s AND period_start=%s")
OG_EARNED = _n("SELECT id, source FROM earned_credits WHERE user_id=%s AND consumed_at IS NULL "
               "ORDER BY granted_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED")
OG_INS_CONS = _n("INSERT INTO consultations (id, user_id, advisor_id, started_at, expires_at, credit_source, created_at) "
                 "VALUES (%s, %s, %s, %s, %s, %s, %s)")
OG_UPD_FF = _n("UPDATE accounts SET first_consultation_used_at=%s WHERE user_id=%s AND first_consultation_used_at IS NULL")
OG_UPD_EARNED = _n("UPDATE earned_credits SET consumed_at=%s, consultation_id=%s WHERE id=%s")
EC_INS = _n("INSERT INTO earned_credits (id, user_id, source, granted_at, consumed_at, "
            "consultation_id, metadata) VALUES (%s, %s, %s, %s, NULL, NULL, %s)")


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
        k = _n(sql)
        p = params or ()
        self.rowcount = -1
        self._r = None
        self._rows = None

        if k == MS_SEL:
            store, key = p
            row = next((r for r in DB["mobile_subscriptions"]
                        if r["store"] == store and r["subscription_key"] == key), None)
            self._r = (row["id"], row["user_id"]) if row else None
        elif k == MS_INS:
            (nid, uid, store, pid, key, ltx, st, ent, pat, cps, eat, ar, lv, pay, cat, uat) = p
            if any(r for r in DB["mobile_subscriptions"]
                   if r["store"] == store and r["subscription_key"] == key):
                self.rowcount = 0
            else:
                DB["mobile_subscriptions"].append(dict(
                    id=nid, user_id=uid, store=store, product_id=pid, subscription_key=key,
                    latest_transaction_id=ltx, status=st, entitled=ent, purchased_at=pat,
                    current_period_start=cps, expires_at=eat, auto_renewing=ar,
                    last_verified_at=lv, raw_payload=pay, created_at=cat, updated_at=uat))
                self.rowcount = 1
        elif k == MS_UPD:
            (pid, ltx, st, ent, cps, eat, ar, lv, pay, uat, sid) = p
            row = next((r for r in DB["mobile_subscriptions"] if r["id"] == sid), None)
            if row:
                row.update(product_id=pid, latest_transaction_id=ltx, status=st, entitled=ent,
                           current_period_start=cps, expires_at=eat, auto_renewing=ar,
                           last_verified_at=lv, raw_payload=pay, updated_at=uat)
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k in (R_ACC, OG_ACC):
            (uid,) = p
            uid = str(uid)
            r = next((a for a in DB["accounts"]
                      if a["user_id"] == uid and a["deleted_at"] is None), None)
            if k == R_ACC:
                self._r = (uid,) if r else None
            else:
                self._r = (uid, r["first_consultation_used_at"]) if r else None
        elif k == R_SUBS:
            (uid,) = p
            uid = str(uid)
            self._rows = [(s["id"], s["current_period_start"], s["expires_at"],
                           s["entitled"], s["store"])
                          for s in DB["mobile_subscriptions"] if s["user_id"] == uid]
        elif k == R_ALW:
            (uid,) = p
            uid = str(uid)
            self._rows = [(a["period_start"], a["period_end"], a["monthly_limit"],
                           a["monthly_used"], a["source_subscription_id"], a["source_period_start"])
                          for a in DB["consultation_allowance"] if a["user_id"] == uid]
        elif k == R_UPE:
            pe, uid, ps = p
            uid = str(uid)
            n = 0
            for a in DB["consultation_allowance"]:
                if a["user_id"] == uid and a["period_start"] == ps:
                    a["period_end"] = pe
                    n += 1
            self.rowcount = n
        elif k == R_UPE_SRC:
            pe, src, sps, uid, ps = p
            uid = str(uid)
            n = 0
            for a in DB["consultation_allowance"]:
                if a["user_id"] == uid and a["period_start"] == ps:
                    a.update(period_end=pe, source_subscription_id=src, source_period_start=sps)
                    n += 1
            self.rowcount = n
        elif k == R_DEL_ALL:
            uid, now = p
            uid = str(uid)
            before = len(DB["consultation_allowance"])
            DB["consultation_allowance"][:] = [
                a for a in DB["consultation_allowance"]
                if not (a["user_id"] == uid and a["period_start"] > now)]
            self.rowcount = before - len(DB["consultation_allowance"])
        elif k == R_DEL_NC:
            uid, now, cid, ccps = p
            uid = str(uid)
            before = len(DB["consultation_allowance"])
            DB["consultation_allowance"][:] = [
                a for a in DB["consultation_allowance"]
                if not (a["user_id"] == uid and a["period_start"] > now
                        and (_distinct(a["source_subscription_id"], cid)
                             or _distinct(a["source_period_start"], ccps)))]
            self.rowcount = before - len(DB["consultation_allowance"])
        elif k == INS_ALW:
            uid, ps, pe, lim, created, src, sps = p
            uid = str(uid)
            if any(a for a in DB["consultation_allowance"]
                   if a["user_id"] == uid and a["period_start"] == ps):
                self.rowcount = 0
            else:
                DB["consultation_allowance"].append(dict(
                    user_id=uid, period_start=ps, period_end=pe, monthly_limit=lim,
                    monthly_used=0, created_at=created, source_subscription_id=src,
                    source_period_start=sps))
                self.rowcount = 1

        elif k == GS_CONS:
            uid, now = p
            uid = str(uid)
            rows = sorted([x for x in DB["consultations"]
                           if x["user_id"] == uid and x["expires_at"] > now],
                          key=lambda x: x["started_at"], reverse=True)
            if rows:
                x = rows[0]
                self._r = (x["id"], x["advisor_id"], x["started_at"], x["expires_at"], x["credit_source"])
        elif k in (GS_ALW, OG_ALW):
            if k == GS_ALW:
                uid, now, _n2 = p
                cols = lambda a: (a["period_start"], a["period_end"], a["monthly_limit"], a["monthly_used"])
            else:
                uid, now, _n2 = p
                cols = lambda a: (a["period_start"], a["monthly_limit"], a["monthly_used"])
            uid = str(uid)
            rows = sorted([a for a in DB["consultation_allowance"]
                           if a["user_id"] == uid and a["period_start"] <= now and a["period_end"] > now],
                          key=lambda a: a["period_start"], reverse=True)
            if rows:
                self._r = cols(rows[0])
        elif k == GS_EARNED:
            (uid,) = p
            uid = str(uid)
            self._r = (sum(1 for e in DB["earned_credits"]
                           if e["user_id"] == uid and e["consumed_at"] is None),)
        elif k == GS_FF:
            (uid,) = p
            uid = str(uid)
            r = next((a for a in DB["accounts"] if a["user_id"] == str(uid)), None)
            self._r = (r["first_consultation_used_at"],) if r is not None else None

        elif k == OG_UPD_USED:
            uid, ps = p
            uid = str(uid)
            r = next((a for a in DB["consultation_allowance"]
                      if a["user_id"] == uid and a["period_start"] == ps), None)
            if r:
                r["monthly_used"] += 1
                self.rowcount = 1
            else:
                self.rowcount = 0
        elif k == OG_EARNED:
            (uid,) = p
            uid = str(uid)
            rows = sorted([e for e in DB["earned_credits"]
                           if e["user_id"] == uid and e["consumed_at"] is None],
                          key=lambda e: e["granted_at"])
            if rows:
                self._r = (rows[0]["id"], rows[0]["source"])
        elif k == OG_INS_CONS:
            cid, uid, adv, st, ex, src, cat = p
            DB["consultations"].append(dict(id=str(cid), user_id=str(uid), advisor_id=adv,
                                            started_at=st, expires_at=ex, credit_source=src, created_at=cat))
            self.rowcount = 1
        elif k == OG_UPD_FF:
            ts, uid = p
            uid = str(uid)
            r = next((a for a in DB["accounts"] if a["user_id"] == uid), None)
            if r is not None and r["first_consultation_used_at"] is None:
                r["first_consultation_used_at"] = ts
                self.rowcount = 1
            else:
                self.rowcount = 0
        elif k == EC_INS:
            eid, uid, src, granted, meta = p
            DB["earned_credits"].append(dict(id=str(eid), user_id=str(uid), source=src,
                                             granted_at=granted, consumed_at=None,
                                             consultation_id=None, metadata=meta))
            self.rowcount = 1
        elif k == OG_UPD_EARNED:
            consumed, cons_id, eid = p
            r = next((e for e in DB["earned_credits"] if e["id"] == str(eid)), None)
            if r:
                r["consumed_at"] = consumed
                r["consultation_id"] = str(cons_id)
                self.rowcount = 1
            else:
                self.rowcount = 0
        else:
            raise AssertionError("SQL non modélisé (E2E B4) : " + k)


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
A.app.config["TESTING"] = True
A.limiter.enabled = False

# La route appelle resync/record/state SANS `now` -> on fige l'horloge pour
# rendre l'E2E déterministe (les scénarios qui font avancer le temps passent
# des dates explicites dans la struct normalisée).
NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
A._utcnow = lambda: NOW

UID = "11111111-1111-4111-8111-111111111111"
UID2 = "22222222-2222-4222-8222-222222222222"
A.resolve_app_session = lambda tok: ({"session_id": "s", "user_id": tok.split(":")[1],
                                      "email": "u@x"} if tok.startswith("T:") else None)
A._google_acknowledge_subscription = lambda *a, **k: {"acknowledged": True}
client = A.app.test_client()

PID = "auryel_premium_monthly"


def seed_account(uid, ff=None):
    DB["accounts"].append({"user_id": uid, "email": "u@x", "deleted_at": None,
                           "first_consultation_used_at": ff})


# Cycle courant "réaliste" : commencé il y a 2 jours, se termine dans 28 jours.
# (current_period_start est TOUJOURS <= now pour un abonnement réellement actif.)
CPS0 = NOW - timedelta(days=2)
EXP0 = NOW + timedelta(days=28)


def norm(store="google_play", key="sub-1", entitled=True, status="SUBSCRIPTION_STATE_ACTIVE",
         cps=None, expires=None, purchased=None, ltx="order-1"):
    return {
        "store": store, "product_id": PID, "subscription_key": key,
        "latest_transaction_id": ltx, "status": status, "entitled": entitled,
        "purchased_at": purchased or (NOW - timedelta(days=40)),
        "current_period_start": cps or CPS0,
        "expires_at": expires or EXP0,
        "auto_renewing": True,
        "raw_payload": {"source": store, "acknowledgement_state": "ACKNOWLEDGEMENT_STATE_PENDING"},
    }


def gverify(n, tok="T:" + UID, body=None):
    b = body or {"store": "google_play", "product_id": PID, "purchase_token": "pt-1"}
    with patch.object(A, "_google_verify_subscription", return_value=n):
        return client.post("/api/billing/verify", json=b, headers={"Authorization": f"Bearer {tok}"})


def averify(n, tok="T:" + UID, body=None):
    b = body or {"store": "app_store", "product_id": PID, "transaction_id": "tx-1"}
    with patch.object(A, "_apple_verify_subscription", return_value=n):
        return client.post("/api/billing/verify", json=b, headers={"Authorization": f"Bearer {tok}"})


def _alw(uid=UID):
    return [a for a in DB["consultation_allowance"] if a["user_id"] == uid]


def _active(uid=UID, now=NOW):
    return [a for a in _alw(uid) if a["period_start"] <= now < a["period_end"]]


print("=" * 64)
print("TEST BILLING VERIFY — END TO END (stores mockés)")
print("=" * 64)

# --- 1. Android : achat actif -> Premium ---------------------------------
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
r = gverify(norm(key="g-1"))
j = r.get_json()
check(r.status_code == 200 and j["quota"]["is_premium"] is True
      and j["quota"]["monthly_limit"] == 4 and j["quota"]["monthly_remaining"] == 4,
      "1 Android actif -> mobile_subscriptions + allowance 4 + quota Premium")
check(len(DB["mobile_subscriptions"]) == 1 and DB["mobile_subscriptions"][0]["entitled"] is True
      and len(_active()) == 1,
      "1b 1 abonnement (entitled True) + 1 allowance active")

# --- 2. reverify -> monthly_used préservé -----------------------------
_active()[0]["monthly_used"] = 3
r = gverify(norm(key="g-1"))
j = r.get_json()
check(r.status_code == 200 and len(DB["mobile_subscriptions"]) == 1
      and len(_active()) == 1 and _active()[0]["monthly_used"] == 3,
      "2 re-verify -> UPDATE, 1 seule allowance, monthly_used=3 préservé")
check(j["quota"]["monthly_used"] == 3 and j["quota"]["monthly_remaining"] == 1,
      "2b quota reflète monthly_used=3")

# --- 3. renouvellement : nouvelle période au bon moment ---------------
cyc2 = EXP0                                  # le cycle suivant démarre à la fin du courant
r = gverify(norm(key="g-1", cps=cyc2, expires=cyc2 + timedelta(days=30)))
check(r.status_code == 200,
      "3 renouvellement anticipé signalé -> 200")
fut = [a for a in _alw() if a["period_start"] == cyc2]
check(len(fut) == 1 and fut[0]["monthly_used"] == 0 and len(_active()) == 1
      and _active()[0]["monthly_used"] == 3,
      "3b future allowance créée au nouveau cycle (used 0), active courante intacte (used 3)")

# revenir à l'état "cycle courant" pour la suite des scénarios 4-6
gverify(norm(key="g-1"))

# --- 4. cancel encore valide -> Premium conservé --------------------
r = gverify(norm(key="g-1", status="SUBSCRIPTION_STATE_CANCELED", entitled=True))
check(r.status_code == 200 and r.get_json()["quota"]["is_premium"] is True
      and _active()[0]["monthly_used"] == 3,
      "4 CANCELED mais entitled True -> Premium conservé, monthly_used intact")

# --- 5. expire -> Premium retiré ---------------------------------
r = gverify(norm(key="g-1", status="SUBSCRIPTION_STATE_EXPIRED", entitled=False))
j = r.get_json()
check(r.status_code == 200 and j["subscription"]["entitled"] is False
      and j["quota"]["is_premium"] is False and len(_active()) == 0,
      "5 EXPIRED / entitled False -> plus d'allowance active, quota non Premium")
check(any(s for s in DB["mobile_subscriptions"] if s["subscription_key"] == "g-1"
          and s["entitled"] is False),
      "5b la ligne mobile_subscriptions reste (entitled False), source de vérité conservée")

# --- 6. grace -> Premium ------------------------------------
r = gverify(norm(key="g-1", status="SUBSCRIPTION_STATE_IN_GRACE_PERIOD", entitled=True))
check(r.status_code == 200 and r.get_json()["quota"]["is_premium"] is True,
      "6 IN_GRACE_PERIOD / entitled True -> Premium ré-accordé (revived, pas de reset)")

# ============================================================
print("-" * 64)

# --- 7. Apple : mêmes scénarios (actif) --------------------
reset()
seed_account(UID2, ff=NOW - timedelta(days=5))
r = averify(norm(store="app_store", key="a-1"), tok="T:" + UID2)
j = r.get_json()
check(r.status_code == 200 and j["subscription"]["store"] == "app_store"
      and j["quota"]["is_premium"] is True and len(_active(UID2)) == 1,
      "7 Apple actif -> Premium, 1 allowance active")

_active(UID2)[0]["monthly_used"] = 2
r = averify(norm(store="app_store", key="a-1"), tok="T:" + UID2)
check(r.get_json()["quota"]["monthly_used"] == 2 and len(_active(UID2)) == 1,
      "7b Apple re-verify -> monthly_used=2 préservé")

r = averify(norm(store="app_store", key="a-1", status="revoked", entitled=False), tok="T:" + UID2)
check(r.get_json()["quota"]["is_premium"] is False and len(_active(UID2)) == 0,
      "7c Apple revoked -> Premium retiré")

# ============================================================
print("-" * 64)

# --- 8. CROSS-STORE : Google actif + Apple actif -> jamais 8 ---------
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
gverify(norm(store="google_play", key="g-x", cps=CPS0, expires=NOW + timedelta(days=20)))
r = averify(norm(store="app_store", key="a-x", cps=CPS0, expires=NOW + timedelta(days=30)))
j = r.get_json()
check(r.status_code == 200 and len(_active()) == 1
      and _active()[0]["monthly_limit"] == 4,
      "8 Google actif PUIS Apple actif -> UNE seule allowance active, quota 4 (jamais 8)")
check(j["quota"]["monthly_limit"] == 4 and j["quota"]["monthly_remaining"] == 4,
      "8b quota renvoyé = 4, pas 8")
check(len(DB["mobile_subscriptions"]) == 2,
      "8c les 2 abonnements coexistent dans mobile_subscriptions")

# --- 9. Google revoked + Apple actif -> Premium reste, used préservé --
_active()[0]["monthly_used"] = 1
r = gverify(norm(store="google_play", key="g-x", status="SUBSCRIPTION_STATE_EXPIRED",
                 entitled=False, cps=CPS0, expires=NOW + timedelta(days=20)))
j = r.get_json()
check(r.status_code == 200 and j["quota"]["is_premium"] is True
      and len(_active()) == 1 and _active()[0]["monthly_used"] == 1,
      "9 Google révoqué mais Apple encore entitled -> Premium maintenu, monthly_used=1 préservé (pas de reset)")

# --- 10. Apple revoked aussi -> plus de Premium -------------
r = averify(norm(store="app_store", key="a-x", status="revoked", entitled=False,
                 cps=CPS0, expires=NOW + timedelta(days=30)))
check(r.get_json()["quota"]["is_premium"] is False and len(_active()) == 0,
      "10 les deux stores révoqués -> Premium retiré, allowances futures supprimées")

# --- 11. replay massif -> aucun double quota -----------
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
for _i in range(6):
    gverify(norm(key="g-r", cps=CPS0, expires=EXP0))
check(len(_active()) == 1 and _active()[0]["monthly_used"] == 0
      and len([a for a in _alw()]) == 1,
      "11 6 verify successifs -> exactement 1 allowance, monthly_used 0 (idempotent)")

# ============================================================
print("-" * 64)

# --- 12. FIRST FREE préservée par un achat Premium vérifié ----------
reset()
seed_account(UID, ff=None)                     # compte neuf : gratuite dispo
r = gverify(norm(key="g-ff"))
j = r.get_json()
check(r.status_code == 200 and j["quota"]["first_free_available"] is True
      and j["quota"]["is_premium"] is True and j["quota"]["monthly_used"] == 0,
      "12 achat Premium vérifié -> first_free_available reste True, monthly_used 0")

# premier message -> consomme la gratuite, PAS le quota Premium
slot = A.open_or_get_consultation(UID, "selena", now=NOW + timedelta(minutes=1))
check(slot["status"] == "opened" and slot["consultation"]["credit_source"] == "first_free",
      "12b 1er message -> credit_source=first_free")
st = A.get_consultation_state(UID, now=NOW + timedelta(minutes=2))
check(st["quota"]["first_free_available"] is False and st["quota"]["monthly_used"] == 0
      and st["quota"]["monthly_remaining"] == 4,
      "12c après la gratuite -> first_free consommée, monthly_used TOUJOURS 0 (4 Premium intacts)")

# consultation suivante (gratuite expirée) -> quota Premium
for c in DB["consultations"]:
    c["expires_at"] = NOW - timedelta(hours=1)
slot2 = A.open_or_get_consultation(UID, "selena", now=NOW + timedelta(hours=3))
st2 = A.get_consultation_state(UID, now=NOW + timedelta(hours=3, minutes=1))
check(slot2["consultation"]["credit_source"] == "monthly" and st2["quota"]["monthly_used"] == 1,
      "12d consultation suivante -> credit_source=monthly, monthly_used 0 -> 1")

# un nouveau verify ne réanime PAS la gratuite ni ne touche monthly_used
r = gverify(norm(key="g-ff"))
st3 = A.get_consultation_state(UID, now=NOW + timedelta(hours=4))
check(st3["quota"]["first_free_available"] is False and st3["quota"]["monthly_used"] == 1,
      "12e re-verify après consommation -> gratuite toujours consommée, monthly_used=1 inchangé")

# ============================================================
print("-" * 64)

# --- 13. EARNED CREDITS intacts pendant tous les verify/resync -------
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
A.grant_earned_credit(UID, "referral", now=NOW - timedelta(days=1))
A.grant_earned_credit(UID, "share_30d", now=NOW - timedelta(days=1))
check(len([e for e in DB["earned_credits"] if e["consumed_at"] is None]) == 2,
      "13 2 earned credits non consommés au départ")
gverify(norm(key="g-e"))
gverify(norm(key="g-e", status="SUBSCRIPTION_STATE_EXPIRED", entitled=False))
gverify(norm(key="g-e"))
n_unused = len([e for e in DB["earned_credits"] if e["consumed_at"] is None])
check(n_unused == 2,
      "13b après verify actif + révocation + re-verify -> 2 earned credits toujours non consommés (jamais touchés)")
st = A.get_consultation_state(UID, now=NOW)
check(st["quota"]["earned_available"] == 2,
      "13c quota.earned_available == 2 inchangé")

# --- 14. account_mismatch cross-user -> 409, rien touché --------
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
seed_account(UID2, ff=NOW - timedelta(days=5))
gverify(norm(key="shared-key"), tok="T:" + UID)
n_alw_uid2_before = len(_alw(UID2))
r = gverify(norm(key="shared-key"), tok="T:" + UID2)
check(r.status_code == 409 and r.get_json()["error"] == "account_mismatch",
      "14 (store, subscription_key) d'un autre compte -> 409 account_mismatch")
check(len(_alw(UID2)) == n_alw_uid2_before
      and len([s for s in DB["mobile_subscriptions"] if s["subscription_key"] == "shared-key"]) == 1
      and DB["mobile_subscriptions"][0]["user_id"] == UID,
      "14b aucune réattribution, aucune allowance créée pour l'autre compte")

# --- 15. monthly_used jamais remis à 0 hors nouvelle période légitime --
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
gverify(norm(key="g-m", cps=CPS0, expires=EXP0))
_active()[0]["monthly_used"] = 4              # quota épuisé
# bascule de porteuse : Apple prend la main (expiry plus lointaine) sur le MÊME
# cycle -> la ligne active est re-pointée, monthly_used JAMAIS remis à zéro.
new_exp = EXP0 + timedelta(days=5)
r = averify(norm(store="app_store", key="a-m", cps=CPS0, expires=new_exp))
check(len(_active()) == 1 and _active()[0]["monthly_used"] == 4
      and _active()[0]["source_subscription_id"] is not None,
      "15 bascule de porteuse sur le même cycle -> re-pointée, monthly_used=4 CONSERVÉ (jamais reset)")
# vrai renouvellement (cps = fin du cycle courant) -> nouvelle période used 0,
# ancienne période used 4 conservée en base.
averify(norm(store="app_store", key="a-m", cps=new_exp, expires=new_exp + timedelta(days=30)))
new_row = [a for a in _alw() if a["period_start"] == new_exp]
old_row = [a for a in _alw() if a["period_start"] == CPS0]
check(len(new_row) == 1 and new_row[0]["monthly_used"] == 0
      and len(old_row) == 1 and old_row[0]["monthly_used"] == 4,
      "15b vrai renouvellement -> NOUVELLE période used 0, ancienne période used 4 conservée")

print("-" * 64)

# --- 16. APPLE BILLING GRACE PERIOD de bout en bout (normalisation RÉELLE) ---
# _apple_normalize est exécuté pour de vrai (pas juste _apple_entitled isolé) ;
# record_mobile_subscription + resync_premium_entitlement + get_consultation_state
# sont réels contre la fausse DB.
from types import SimpleNamespace as _SN
_MSx = lambda dt: int(dt.timestamp() * 1000)


def _apple_grace_payloads(grace_end):
    tx = _SN(productId=PID, transactionId="tx-grace",
             originalTransactionId="otx-grace",
             purchaseDate=_MSx(NOW - timedelta(days=27)),
             originalPurchaseDate=_MSx(NOW - timedelta(days=90)),
             expiresDate=_MSx(NOW - timedelta(days=1)),      # transaction EXPIRÉE
             revocationDate=None, type="Auto-Renewable Subscription")
    rn = _SN(autoRenewStatus=1, expirationIntent=None, isInBillingRetryPeriod=True,
             gracePeriodExpiresDate=_MSx(grace_end))
    return tx, rn


reset()
seed_account(UID2, ff=NOW - timedelta(days=5))

# (a) grâce ENCORE active : gracePeriodExpiresDate future
_g_active = NOW + timedelta(days=13)
_tx_a, _rn_a = _apple_grace_payloads(_g_active)
_norm_a = A._apple_normalize(_tx_a, _rn_a, 4, "production", PID, NOW)
check(_norm_a["entitled"] is True and _norm_a["expires_at"] == _g_active
      and _norm_a["current_period_start"] == NOW - timedelta(days=27),
      "16 _apple_normalize grâce active -> entitled True, expires_at = gracePeriodExpiresDate")

r = averify(_norm_a, tok="T:" + UID2)
check(r.status_code == 200 and r.get_json()["quota"]["is_premium"] is True,
      "16b pipeline réel : transaction expirée MAIS grâce active -> Premium RESTE actif")
_row = [s for s in DB["mobile_subscriptions"] if s["subscription_key"] == "otx-grace"][0]
check(_row["expires_at"] == _g_active and _row["entitled"] is True,
      "16c record_mobile_subscription.expires_at = gracePeriodExpiresDate (pas la fin de transaction)")
check(len(_active(UID2)) == 1 and _active(UID2)[0]["period_end"] == _g_active,
      "16d allowance active jusqu'à gracePeriodExpiresDate")

# (b) grâce TERMINÉE : gracePeriodExpiresDate <= now
_tx_b, _rn_b = _apple_grace_payloads(NOW - timedelta(hours=2))
_norm_b = A._apple_normalize(_tx_b, _rn_b, 4, "production", PID, NOW)
check(_norm_b["entitled"] is False and _norm_b["expires_at"] <= NOW,
      "16e _apple_normalize après fin de grâce -> entitled False, expires_at passée")
r = averify(_norm_b, tok="T:" + UID2)
check(r.status_code == 200 and r.get_json()["quota"]["is_premium"] is False
      and len(_active(UID2)) == 0,
      "16f pipeline réel : gracePeriodExpiresDate dépassée -> Premium retiré (resync)")

print("-" * 64)
print(f"RÉSULTAT : {_S['pass']} ok / {_S['fail']} ko")
sys.exit(1 if _S["fail"] else 0)
