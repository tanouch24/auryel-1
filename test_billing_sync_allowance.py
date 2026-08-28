"""
test_billing_sync_allowance.py — B3-B : resync_premium_entitlement(user_id, now).

Re-projection du quota Premium (consultation_allowance) depuis l'état réel des
abonnements mobiles (mobile_subscriptions). 100 % local : psycopg2 mocké,
get_conn -> FakeConn/FakeCursor (fausse DB en mémoire, verrou de ligne accounts
pour FOR UPDATE). get_consultation_state / open_or_get_consultation ne sont PAS
modifiés — on les appelle tels quels pour lire is_premium / monthly_used.
Style aligné sur test_consultation_engine.py (script, pas pytest).
"""

import sys
import threading
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

from datetime import datetime, timezone, timedelta

import auryel_bot as A

_STATE = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _STATE["pass"] += 1
        print(f"✅ {label}")
    else:
        _STATE["fail"] += 1
        print(f"❌ {label}")


def _norm(sql):
    return " ".join(sql.split())


def _distinct(a, b):
    """SQL `a IS DISTINCT FROM b`."""
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    return a != b


# ---------------------------------------------------------------------------
# Fausse DB
# ---------------------------------------------------------------------------
DB = {"accounts": [], "mobile_subscriptions": [],
      "consultation_allowance": [], "earned_credits": [], "consultations": []}
_STORE_LOCK = threading.RLock()
_ROW_LOCKS = {}
_ROW_LOCKS_GUARD = threading.Lock()
_CONN_COUNT = [0]


def _row_lock(key):
    with _ROW_LOCKS_GUARD:
        lk = _ROW_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _ROW_LOCKS[key] = lk
        return lk


def reset():
    with _STORE_LOCK:
        for v in DB.values():
            v.clear()
    with _ROW_LOCKS_GUARD:
        _ROW_LOCKS.clear()
    _CONN_COUNT[0] = 0


SEL_ACC_LOCK = _norm("SELECT user_id FROM accounts "
                     "WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE")
SEL_SUBS = _norm("SELECT id, current_period_start, expires_at, entitled, store "
                 "FROM mobile_subscriptions WHERE user_id=%s")
SEL_ALW_LOCK = _norm("SELECT period_start, period_end, monthly_limit, monthly_used, "
                     "source_subscription_id, source_period_start "
                     "FROM consultation_allowance WHERE user_id=%s FOR UPDATE")
UPD_PE = _norm("UPDATE consultation_allowance SET period_end=%s "
               "WHERE user_id=%s AND period_start=%s")
UPD_PE_SRC = _norm("UPDATE consultation_allowance SET period_end=%s, "
                   "source_subscription_id=%s, source_period_start=%s "
                   "WHERE user_id=%s AND period_start=%s")
DEL_FUT_ALL = _norm("DELETE FROM consultation_allowance "
                    "WHERE user_id=%s AND period_start > %s")
DEL_FUT_NOT_CARRIER = _norm(
    "DELETE FROM consultation_allowance WHERE user_id=%s AND period_start > %s "
    "AND (source_subscription_id IS DISTINCT FROM %s "
    "     OR source_period_start IS DISTINCT FROM %s)")
INS_ALW = _norm(
    "INSERT INTO consultation_allowance (user_id, period_start, period_end, "
    "monthly_limit, monthly_used, created_at, source_subscription_id, "
    "source_period_start) VALUES (%s, %s, %s, %s, 0, %s, %s, %s) "
    "ON CONFLICT (user_id, period_start) DO NOTHING")

# get_consultation_state (INCHANGÉ)
GS_CONS = _norm("SELECT id, advisor_id, started_at, expires_at, credit_source "
                "FROM consultations WHERE user_id=%s AND expires_at > %s "
                "ORDER BY started_at DESC LIMIT 1")
GS_ALW = _norm("SELECT period_start, period_end, monthly_limit, monthly_used "
               "FROM consultation_allowance WHERE user_id=%s AND period_start <= %s "
               "AND period_end > %s ORDER BY period_start DESC LIMIT 1")
GS_EARNED = _norm("SELECT COUNT(*) FROM earned_credits "
                  "WHERE user_id=%s AND consumed_at IS NULL")
GS_FF = _norm("SELECT first_consultation_used_at FROM accounts WHERE user_id=%s")


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._result = None
        self._rows = None
        self.rowcount = -1

    def fetchone(self):
        return self._result

    def fetchall(self):
        return list(self._rows or [])

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = _norm(sql)
        p = params or ()
        self.rowcount = -1
        self._result = None
        self._rows = None

        # ---- resync: mutex accounts ----
        if k == SEL_ACC_LOCK:
            (uid,) = p
            uid = str(uid)
            lk = _row_lock("acc:" + uid)
            lk.acquire()
            self._conn._locks.append(lk)
            with _STORE_LOCK:
                r = next((a for a in DB["accounts"]
                          if a["user_id"] == uid and a["deleted_at"] is None), None)
            self._result = (uid,) if r else None

        elif k == SEL_SUBS:
            (uid,) = p
            uid = str(uid)
            with _STORE_LOCK:
                self._rows = [(s["id"], s["current_period_start"], s["expires_at"],
                               s["entitled"], s["store"])
                              for s in DB["mobile_subscriptions"] if s["user_id"] == uid]

        elif k == SEL_ALW_LOCK:
            (uid,) = p
            uid = str(uid)
            with _STORE_LOCK:
                self._rows = [(a["period_start"], a["period_end"], a["monthly_limit"],
                               a["monthly_used"], a["source_subscription_id"],
                               a["source_period_start"])
                              for a in DB["consultation_allowance"] if a["user_id"] == uid]

        elif k == UPD_PE:
            pe, uid, ps = p
            uid = str(uid)
            with _STORE_LOCK:
                n = 0
                for a in DB["consultation_allowance"]:
                    if a["user_id"] == uid and a["period_start"] == ps:
                        a["period_end"] = pe
                        n += 1
            self.rowcount = n

        elif k == UPD_PE_SRC:
            pe, src, sps, uid, ps = p
            uid = str(uid)
            with _STORE_LOCK:
                n = 0
                for a in DB["consultation_allowance"]:
                    if a["user_id"] == uid and a["period_start"] == ps:
                        a["period_end"] = pe
                        a["source_subscription_id"] = src
                        a["source_period_start"] = sps
                        n += 1
            self.rowcount = n

        elif k == DEL_FUT_ALL:
            uid, now = p
            uid = str(uid)
            with _STORE_LOCK:
                before = len(DB["consultation_allowance"])
                DB["consultation_allowance"][:] = [
                    a for a in DB["consultation_allowance"]
                    if not (a["user_id"] == uid and a["period_start"] > now)]
                self.rowcount = before - len(DB["consultation_allowance"])

        elif k == DEL_FUT_NOT_CARRIER:
            uid, now, c_id, c_cps = p
            uid = str(uid)
            with _STORE_LOCK:
                before = len(DB["consultation_allowance"])
                DB["consultation_allowance"][:] = [
                    a for a in DB["consultation_allowance"]
                    if not (a["user_id"] == uid and a["period_start"] > now
                            and (_distinct(a["source_subscription_id"], c_id)
                                 or _distinct(a["source_period_start"], c_cps)))]
                self.rowcount = before - len(DB["consultation_allowance"])

        elif k == INS_ALW:
            uid, ps, pe, lim, created, src, sps = p
            uid = str(uid)
            with _STORE_LOCK:
                exists = any(a for a in DB["consultation_allowance"]
                             if a["user_id"] == uid and a["period_start"] == ps)
                if exists:
                    self.rowcount = 0
                else:
                    DB["consultation_allowance"].append({
                        "user_id": uid, "period_start": ps, "period_end": pe,
                        "monthly_limit": lim, "monthly_used": 0, "created_at": created,
                        "source_subscription_id": src, "source_period_start": sps,
                    })
                    self.rowcount = 1

        # ---- get_consultation_state (inchangé) ----
        elif k == GS_CONS:
            uid, now = p
            uid = str(uid)
            with _STORE_LOCK:
                rows = [x for x in DB["consultations"]
                        if x["user_id"] == uid and x["expires_at"] > now]
            rows.sort(key=lambda x: x["started_at"], reverse=True)
            if rows:
                x = rows[0]
                self._result = (x["id"], x["advisor_id"], x["started_at"],
                                x["expires_at"], x["credit_source"])

        elif k == GS_ALW:
            uid, now, _now2 = p
            uid = str(uid)
            with _STORE_LOCK:
                rows = [a for a in DB["consultation_allowance"]
                        if a["user_id"] == uid and a["period_start"] <= now
                        and a["period_end"] > now]
            rows.sort(key=lambda a: a["period_start"], reverse=True)
            if rows:
                a0 = rows[0]
                self._result = (a0["period_start"], a0["period_end"],
                                a0["monthly_limit"], a0["monthly_used"])

        elif k == GS_EARNED:
            (uid,) = p
            uid = str(uid)
            with _STORE_LOCK:
                n = sum(1 for e in DB["earned_credits"]
                        if e["user_id"] == uid and e["consumed_at"] is None)
            self._result = (n,)

        elif k == GS_FF:
            (uid,) = p
            uid = str(uid)
            with _STORE_LOCK:
                r = next((a for a in DB["accounts"] if a["user_id"] == uid), None)
            self._result = (r["first_consultation_used_at"],) if r is not None else None

        else:
            raise AssertionError("SQL non modélisé par le fake B3-B : " + k)


class FakeConn:
    def __init__(self):
        self._locks = []

    def cursor(self):
        return FakeCursor(self)

    def _release(self):
        for lk in self._locks:
            try:
                lk.release()
            except RuntimeError:
                pass
        self._locks = []

    def commit(self):
        self._release()

    def rollback(self):
        self._release()

    def close(self):
        self._release()


def _counting_get_conn():
    _CONN_COUNT[0] += 1
    return FakeConn()


A.get_conn = _counting_get_conn

# ---------------------------------------------------------------------------
UID = "aaaaaaaa-1111-4aaa-8aaa-aaaaaaaaaaaa"
G_ID = "11111111-2222-4111-8111-111111111111"
A_ID = "22222222-3333-4222-8222-222222222222"


def dt(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def seed_account(uid=UID, deleted_at=None, first_consultation_used_at=None):
    with _STORE_LOCK:
        DB["accounts"].append({"user_id": uid, "deleted_at": deleted_at,
                               "first_consultation_used_at": first_consultation_used_at})


def seed_sub(sub_id, store, cps, expires, entitled, uid=UID):
    with _STORE_LOCK:
        DB["mobile_subscriptions"].append({
            "id": sub_id, "user_id": uid, "store": store,
            "current_period_start": cps, "expires_at": expires,
            "entitled": entitled})


def set_sub(sub_id, **kw):
    with _STORE_LOCK:
        for s in DB["mobile_subscriptions"]:
            if s["id"] == sub_id:
                s.update(kw)


def seed_alw(ps, pe, used=0, src=None, sps=None, limit=4, uid=UID):
    with _STORE_LOCK:
        DB["consultation_allowance"].append({
            "user_id": uid, "period_start": ps, "period_end": pe,
            "monthly_limit": limit, "monthly_used": used, "created_at": ps,
            "source_subscription_id": src, "source_period_start": sps})


def alw(uid=UID):
    with _STORE_LOCK:
        return sorted((a for a in DB["consultation_allowance"] if a["user_id"] == uid),
                      key=lambda a: a["period_start"])


def state(now, uid=UID):
    return A.get_consultation_state(uid, now=now)


def resync(now, uid=UID):
    return A.resync_premium_entitlement(uid, now=now)


print("=" * 64)
print("TEST BILLING SYNC ALLOWANCE — B3-B")
print("=" * 64)

# --- 1. premier abonnement actif -> allowance 4 / used 0 --------------------
reset(); seed_account()
seed_sub(G_ID, "google_play", dt(2026, 8, 15), dt(2026, 9, 15), True)
r = resync(dt(2026, 8, 20))
check(r["premium"] is True and r["action"] == "provisioned" and len(alw()) == 1,
      "1a premier abonnement -> action=provisioned, 1 allowance")
_a = alw()[0]
check(_a["period_start"] == dt(2026, 8, 15) and _a["period_end"] == dt(2026, 9, 15)
      and _a["monthly_limit"] == 4 and _a["monthly_used"] == 0
      and _a["source_subscription_id"] == G_ID
      and _a["source_period_start"] == dt(2026, 8, 15),
      "1b allowance 4 / used 0 / source = Google")
_st = state(dt(2026, 8, 20))
check(_st["quota"]["is_premium"] is True and _st["quota"]["monthly_used"] == 0
      and _st["quota"]["monthly_remaining"] == 4,
      "1c get_consultation_state -> is_premium, remaining 4")

# --- 2. reverify même cycle -> 1 ligne, monthly_used préservé --------------
with _STORE_LOCK:
    alw()[0]["monthly_used"] = 2
r = resync(dt(2026, 8, 21))
check(r["action"] == "noop" and len(alw()) == 1 and alw()[0]["monthly_used"] == 2,
      "2 reverify même cycle -> noop, 1 ligne, monthly_used=2 préservé")

# --- 3. vrai renouvellement anticipé -> future créée, active intacte -------
reset(); seed_account()
seed_sub(G_ID, "google_play", dt(2026, 9, 15), dt(2026, 10, 15), True)
seed_alw(dt(2026, 8, 15), dt(2026, 9, 15), used=3, src=G_ID, sps=dt(2026, 8, 15))
r = resync(dt(2026, 9, 10))
check(r["action"] == "renewal_scheduled" and len(alw()) == 2,
      "3a renouvellement anticipé -> renewal_scheduled, 2 lignes (active + future)")
_act = next(a for a in alw() if a["period_start"] == dt(2026, 8, 15))
_fut = next(a for a in alw() if a["period_start"] == dt(2026, 9, 15))
check(_act["monthly_used"] == 3 and _act["period_end"] == dt(2026, 9, 15),
      "3b allowance active INTACTE (used 3, period_end 15 sept)")
check(_fut["monthly_used"] == 0 and _fut["period_end"] == dt(2026, 10, 15)
      and _fut["source_subscription_id"] == G_ID
      and _fut["source_period_start"] == dt(2026, 9, 15),
      "3c future : used 0, [15 sept -> 15 oct], source = Google/cycle 15 sept")
check(state(dt(2026, 9, 10))["quota"]["monthly_used"] == 3,
      "3d avant la frontière -> get_state lit encore used 3")

# --- 4. passage de la frontière -> future devient active -> 4 fraîches -----
_st = state(dt(2026, 9, 16))
check(_st["quota"]["is_premium"] is True and _st["quota"]["monthly_used"] == 0
      and _st["quota"]["monthly_remaining"] == 4,
      "4a après la frontière -> get_state lit la future : used 0 / remaining 4")
r = resync(dt(2026, 9, 16))
check(r["action"] == "noop" and len(alw()) == 2,
      "4b resync après frontière -> noop (future = même cycle porteuse), 2 lignes")

# --- 5. deuxième store devient porteuse -> repoint, pas de nouvelle ligne --
reset(); seed_account()
seed_sub(G_ID, "google_play", dt(2026, 8, 15), dt(2026, 9, 15), True)
seed_sub(A_ID, "app_store", dt(2026, 8, 20), dt(2026, 9, 25), True)
seed_alw(dt(2026, 8, 15), dt(2026, 9, 15), used=3, src=G_ID, sps=dt(2026, 8, 15))
r = resync(dt(2026, 9, 1))
check(r["action"] == "repointed" and len(alw()) == 1,
      "5a Apple (expires plus tard) devient porteuse -> repointed, TOUJOURS 1 ligne")
_a = alw()[0]
check(_a["period_start"] == dt(2026, 8, 15) and _a["monthly_used"] == 3
      and _a["period_end"] == dt(2026, 9, 25)
      and _a["source_subscription_id"] == A_ID
      and _a["source_period_start"] == dt(2026, 8, 20),
      "5b même ligne : period_start & used 3 inchangés ; period_end + source -> Apple")
check(state(dt(2026, 9, 1))["quota"]["monthly_used"] == 3
      and state(dt(2026, 9, 1))["quota"]["monthly_limit"] == 4,
      "5c get_state : used 3 préservé, monthly_limit 4 (pas 8)")

# --- 6. BUG B3.3 : future Google puis Apple porteuse -> future Google DELETE
reset(); seed_account()
seed_sub(G_ID, "google_play", dt(2026, 8, 15), dt(2026, 9, 15), True)
seed_alw(dt(2026, 8, 15), dt(2026, 9, 15), used=3, src=G_ID, sps=dt(2026, 8, 15))
# Google annonce son renouvellement -> future Google créée
set_sub(G_ID, current_period_start=dt(2026, 9, 15), expires_at=dt(2026, 10, 15))
resync(dt(2026, 9, 10))
check(len(alw()) == 2, "6a future Google créée (2 lignes)")
# Apple devient entitled avec expires encore plus tard -> Apple porteuse
seed_sub(A_ID, "app_store", dt(2026, 9, 5), dt(2026, 11, 1), True)
r = resync(dt(2026, 9, 12))
check(r["action"] == "repointed",
      "6b Apple devient porteuse -> repointed")
check(len(alw()) == 1 and alw()[0]["source_subscription_id"] == A_ID,
      "6c la FUTURE Google est SUPPRIMÉE (Google reste entitled) -> 1 seule ligne, source Apple")
check(alw()[0]["monthly_used"] == 3 and alw()[0]["period_start"] == dt(2026, 8, 15),
      "6d ligne re-pointée : used 3, period_start inchangé")
# passage de l'ancienne date future Google (15 sept) : PAS de reset
_st = state(dt(2026, 9, 15))
check(_st["quota"]["is_premium"] is True and _st["quota"]["monthly_used"] == 3,
      "6e AU 15 SEPT (ancienne date future Google) -> monthly_used TOUJOURS 3, AUCUN reset")
check(state(dt(2026, 9, 20))["quota"]["monthly_used"] == 3,
      "6f encore used 3 après le 15 sept (période Apple jusqu'au 1er nov)")

# --- 7. porteuse révoquée -> autre store actif -> repoint sans reset ------
reset(); seed_account()
seed_sub(A_ID, "app_store", dt(2026, 8, 20), dt(2026, 9, 25), True)
seed_sub(G_ID, "google_play", dt(2026, 8, 15), dt(2026, 9, 15), True)
seed_alw(dt(2026, 8, 20), dt(2026, 9, 25), used=2, src=A_ID, sps=dt(2026, 8, 20))
set_sub(A_ID, entitled=False)                       # Apple révoqué (expires encore futur)
r = resync(dt(2026, 9, 1))
check(r["action"] == "repointed" and len(alw()) == 1,
      "7a porteuse Apple révoquée, Google actif -> repointed, 1 ligne")
_a = alw()[0]
check(_a["monthly_used"] == 2 and _a["period_start"] == dt(2026, 8, 20)
      and _a["source_subscription_id"] == G_ID
      and _a["period_end"] == dt(2026, 9, 15),
      "7b used 2 & period_start inchangés ; source -> Google, period_end -> Google.expires")
check(state(dt(2026, 9, 1))["quota"]["is_premium"] is True
      and state(dt(2026, 9, 1))["quota"]["monthly_used"] == 2,
      "7c Premium ne coupe pas, used 2 préservé")

# --- 8. retour ancienne porteuse -> pas de +4 immédiat -------------------
set_sub(A_ID, entitled=True, current_period_start=dt(2026, 9, 10),
        expires_at=dt(2026, 10, 20))               # Apple revient (expires le + tard)
r = resync(dt(2026, 9, 5))
check(r["action"] == "repointed" and len(alw()) == 1,
      "8a retour Apple -> repointed (PAS provisioned), 1 ligne")
_a = alw()[0]
check(_a["monthly_used"] == 2 and _a["period_start"] == dt(2026, 8, 20)
      and _a["source_subscription_id"] == A_ID
      and _a["period_end"] == dt(2026, 10, 20),
      "8b used 2 & period_start inchangés -> AUCUN +4 immédiat")

# --- 9. après retour, vrai renouvellement -> 4 fraîches à la vraie frontière
set_sub(A_ID, current_period_start=dt(2026, 10, 20), expires_at=dt(2026, 11, 20))
r = resync(dt(2026, 10, 18))
check(r["action"] == "renewal_scheduled" and len(alw()) == 2,
      "9a Apple renouvelle réellement -> renewal_scheduled, future créée")
check(state(dt(2026, 10, 19))["quota"]["monthly_used"] == 2,
      "9b avant frontière -> used 2")
check(state(dt(2026, 10, 21))["quota"]["monthly_used"] == 0
      and state(dt(2026, 10, 21))["quota"]["monthly_remaining"] == 4,
      "9c après frontière -> 4 fraîches (used 0)")

# --- 10. deux stores chevauchants -> jamais quota 8 --------------------
reset(); seed_account()
seed_sub(G_ID, "google_play", dt(2026, 8, 15), dt(2026, 9, 15), True)
seed_sub(A_ID, "app_store", dt(2026, 8, 20), dt(2026, 9, 20), True)
resync(dt(2026, 9, 1))
resync(dt(2026, 9, 1))                              # 2e passage
check(len(alw()) == 1 and alw()[0]["monthly_limit"] == 4,
      "10a deux stores actifs -> 1 SEULE allowance, monthly_limit 4 (jamais 8)")
check(state(dt(2026, 9, 1))["quota"]["monthly_limit"] == 4,
      "10b get_state : monthly_limit 4")

# --- 11. plus aucun entitled -> active raccourcie, futures DELETE --------
reset(); seed_account()
seed_sub(G_ID, "google_play", dt(2026, 8, 15), dt(2026, 9, 15), True)
seed_alw(dt(2026, 8, 15), dt(2026, 9, 15), used=3, src=G_ID, sps=dt(2026, 8, 15))
seed_alw(dt(2026, 9, 15), dt(2026, 10, 15), used=0, src=G_ID, sps=dt(2026, 9, 15))
set_sub(G_ID, entitled=False)
r = resync(dt(2026, 9, 1))
check(r["premium"] is False and r["action"] == "deactivated",
      "11a plus aucun entitled -> premium False, action deactivated")
_rows = alw()
check(len(_rows) == 1 and _rows[0]["period_end"] == dt(2026, 9, 1)
      and _rows[0]["monthly_used"] == 3,
      "11b active raccourcie à now ; future SUPPRIMÉE ; monthly_used 3 préservé")
check(state(dt(2026, 9, 1))["quota"]["is_premium"] is False
      and state(dt(2026, 9, 1))["quota"]["monthly_limit"] == 0,
      "11c get_state : is_premium False, quota 0")

# --- 12. entitled=NULL -> aucun Premium --------------------------------
reset(); seed_account()
seed_sub(G_ID, "google_play", dt(2026, 8, 15), dt(2026, 9, 15), None)
r = resync(dt(2026, 9, 1))
check(r["premium"] is False and len(alw()) == 0,
      "12 entitled=NULL -> aucun Premium, aucune allowance")

# --- 13. entitled=false -> aucun Premium ------------------------------
reset(); seed_account()
seed_sub(G_ID, "google_play", dt(2026, 8, 15), dt(2026, 9, 15), False)
r = resync(dt(2026, 9, 1))
check(r["premium"] is False and len(alw()) == 0,
      "13 entitled=false -> aucun Premium, aucune allowance")

# --- 14. first_free intacte ------------------------------------------
reset(); seed_account(first_consultation_used_at=None)
seed_sub(G_ID, "google_play", dt(2026, 8, 15), dt(2026, 9, 15), True)
resync(dt(2026, 9, 1))
check(DB["accounts"][0]["first_consultation_used_at"] is None
      and state(dt(2026, 9, 1))["quota"]["first_free_available"] is True,
      "14 first_consultation_used_at JAMAIS touché ; first_free_available reste True")

# --- 15. earned_credits intacts ------------------------------------
reset(); seed_account()
with _STORE_LOCK:
    DB["earned_credits"].append({"id": "ec1", "user_id": UID, "consumed_at": None})
seed_sub(G_ID, "google_play", dt(2026, 8, 15), dt(2026, 9, 15), True)
resync(dt(2026, 9, 1))
resync(dt(2026, 9, 1))
check(len(DB["earned_credits"]) == 1 and DB["earned_credits"][0]["consumed_at"] is None
      and state(dt(2026, 9, 1))["quota"]["earned_available"] == 1,
      "15 earned_credits jamais touchés (le fake exploserait sur toute requête earned du resync)")

# --- 16. resync N fois -> idempotent ------------------------------
reset(); seed_account()
seed_sub(G_ID, "google_play", dt(2026, 8, 15), dt(2026, 9, 15), True)
_actions = [resync(dt(2026, 9, 1))["action"] for _ in range(5)]
check(_actions == ["provisioned", "noop", "noop", "noop", "noop"]
      and len(alw()) == 1 and alw()[0]["monthly_used"] == 0,
      "16 resync x5 -> provisioned puis noop, 1 ligne, used 0 stable")

# --- 17. concurrence : 2 resync simultanés -> 1 trajectoire -----------
reset(); seed_account()
seed_sub(G_ID, "google_play", dt(2026, 8, 15), dt(2026, 9, 15), True)
_res17 = []
_lk17 = threading.Lock()


def _w17():
    rr = A.resync_premium_entitlement(UID, now=dt(2026, 9, 1))
    with _lk17:
        _res17.append(rr)


_th = [threading.Thread(target=_w17) for _ in range(2)]
for t in _th:
    t.start()
for t in _th:
    t.join()
check(len(alw()) == 1,
      "17a 2 resync simultanés -> 1 SEULE allowance (aucun double provisionnement)")
check(sorted(r["action"] for r in _res17) == ["noop", "provisioned"],
      "17b un thread provisionne, l'autre no-op")
check(len([a for a in alw() if a["period_start"] > dt(2026, 9, 1)]) == 0,
      "17c aucune future parasite")

# --- 18. _insert_allowance reste sur la MÊME connexion du resync -------
reset(); seed_account()
seed_sub(G_ID, "google_play", dt(2026, 8, 15), dt(2026, 9, 15), True)
_CONN_COUNT[0] = 0
resync(dt(2026, 9, 1))                              # provisionne -> appelle _insert_allowance
check(_CONN_COUNT[0] == 1,
      "18 resync (avec _insert_allowance) ouvre EXACTEMENT une connexion")

# --- 19. anomalie : plusieurs allowances actives + carrier valide -------
reset(); seed_account()
seed_sub(G_ID, "google_play", dt(2026, 8, 15), dt(2026, 10, 15), True)
seed_alw(dt(2026, 8, 15), dt(2026, 9, 15), used=3, src=G_ID, sps=dt(2026, 8, 15))
seed_alw(dt(2026, 9, 1), dt(2026, 10, 1), used=1, src=G_ID, sps=dt(2026, 9, 1))
r = resync(dt(2026, 9, 5))
check(r["anomaly"] == "multiple_active_allowances",
      "19a anomalie multi-actives SIGNALÉE dans le résultat")
check(len(alw()) == 2,
      "19b aucune ligne supprimée, aucun quota en plus (toujours 2 lignes)")
_old = next(a for a in alw() if a["period_start"] == dt(2026, 8, 15))
_kept = next(a for a in alw() if a["period_start"] == dt(2026, 9, 1))
check(_old["period_end"] == dt(2026, 9, 5) and _old["monthly_used"] == 3,
      "19c l'autre ligne active est SORTIE de l'état actif (period_end=now), used 3 inchangé")
check(_kept["period_end"] == dt(2026, 10, 15) and _kept["monthly_used"] == 1,
      "19d la ligne retenue (period_start le + récent) reste active, used 1 inchangé")
_st = state(dt(2026, 9, 5))
check(_st["quota"]["monthly_used"] == 1 and _st["quota"]["is_premium"] is True,
      "19e get_state : UNE seule période active, used 1")
r2 = resync(dt(2026, 9, 5))
check(r2["anomaly"] is None and len(alw()) == 2,
      "19f resync rejoué -> PLUS d'anomalie (le nettoyage a persisté), toujours 2 lignes")

# --- 20. compte absent -> unknown_account ---------------------------
reset()
r = resync(dt(2026, 9, 1), uid="00000000-0000-4000-8000-000000000000")
check(r["premium"] is False and r["action"] == "unknown_account"
      and len(DB["consultation_allowance"]) == 0,
      "20 compte absent -> unknown_account, rien écrit")

# --- 21. (B3-B.1 / A) 2 actives + AUCUN entitled -> désactivation totale ---
reset(); seed_account()
seed_sub(G_ID, "google_play", dt(2026, 8, 15), dt(2026, 10, 15), False)   # non entitled
seed_alw(dt(2026, 8, 10), dt(2026, 9, 20), used=4, src=G_ID, sps=dt(2026, 8, 10))
seed_alw(dt(2026, 9, 1), dt(2026, 10, 5), used=2, src=G_ID, sps=dt(2026, 9, 1))
r = resync(dt(2026, 9, 8))
check(r["premium"] is False and r["action"] == "deactivated"
      and r["anomaly"] == "multiple_active_allowances",
      "21a 2 actives + aucun entitled -> premium False, deactivated, anomaly signalée")
_r21 = alw()
check(len(_r21) == 2
      and all(a["period_end"] == dt(2026, 9, 8) for a in _r21),
      "21b LES DEUX lignes sortent de l'état actif (period_end=now), aucune supprimée")
check(_r21[0]["monthly_used"] == 4 and _r21[1]["monthly_used"] == 2,
      "21c monthly_used de chaque ligne INCHANGÉ (4 et 2)")
check(state(dt(2026, 9, 8))["quota"]["is_premium"] is False,
      "21d get_state : is_premium False (aucune allowance active après désactivation)")

# --- 22. (B3-B.1 / B) 3 actives + carrier valide -------------------------
reset(); seed_account()
seed_sub(G_ID, "google_play", dt(2026, 8, 15), dt(2026, 11, 1), True)
seed_alw(dt(2026, 8, 1), dt(2026, 9, 15), used=4, src=G_ID, sps=dt(2026, 8, 1))
seed_alw(dt(2026, 8, 10), dt(2026, 9, 20), used=2, src=G_ID, sps=dt(2026, 8, 10))
seed_alw(dt(2026, 8, 20), dt(2026, 10, 1), used=1, src=G_ID, sps=dt(2026, 8, 20))
r = resync(dt(2026, 8, 25))
check(r["anomaly"] == "multiple_active_allowances" and len(alw()) == 3,
      "22a 3 actives -> anomaly signalée, 3 lignes conservées")
_by_ps = {a["period_start"]: a for a in alw()}
check(_by_ps[dt(2026, 8, 1)]["period_end"] == dt(2026, 8, 25)
      and _by_ps[dt(2026, 8, 10)]["period_end"] == dt(2026, 8, 25),
      "22b les 2 lignes NON retenues -> period_end = now")
_kept22 = _by_ps[dt(2026, 8, 20)]
check(_kept22["period_start"] == dt(2026, 8, 20)
      and _kept22["period_end"] == dt(2026, 11, 1),   # repointée/alignée sur carrier
      "22c la ligne au period_start le + récent reste la seule active")
check(_by_ps[dt(2026, 8, 1)]["monthly_used"] == 4
      and _by_ps[dt(2026, 8, 10)]["monthly_used"] == 2
      and _kept22["monthly_used"] == 1,
      "22d monthly_used des 3 lignes INCHANGÉ (4 / 2 / 1)")
check(sum(1 for a in alw() if a["period_start"] <= dt(2026, 8, 25) < a["period_end"]) == 1,
      "22e exactement UNE allowance active après le resync")

# --- 23. (B3-B.1 / C) second resync du cas B -> plus d'anomalie ---------
r23 = resync(dt(2026, 8, 25))
check(r23["anomaly"] is None,
      "23a 2e resync -> plus d'anomalie multiple-active")
check(sum(1 for a in alw() if a["period_start"] <= dt(2026, 8, 25) < a["period_end"]) == 1
      and len(alw()) == 3,
      "23b exactement 1 active, 3 lignes (idempotent, rien de nouveau)")
check([a["monthly_used"] for a in alw()] == [4, 2, 1],
      "23c monthly_used des 3 lignes toujours [4, 2, 1] après le 2e resync")

# --- 24. (B3-B.1 / D) carrier déterministe : expires_at + store égaux ---
_ID_LOW = "10000000-0000-4000-8000-000000000000"
_ID_HIGH = "90000000-0000-4000-8000-000000000000"
_exp = dt(2026, 9, 15)
_cps_low = dt(2026, 8, 10)
_cps_high = dt(2026, 8, 20)


def _run24(order):
    reset(); seed_account()
    for sid, cps in order:
        seed_sub(sid, "google_play", cps, _exp, True)
    resync(dt(2026, 9, 1))
    a0 = alw()[0]
    return (a0["source_subscription_id"], a0["source_period_start"])


_res_a = _run24([(_ID_LOW, _cps_low), (_ID_HIGH, _cps_high)])
_res_b = _run24([(_ID_HIGH, _cps_high), (_ID_LOW, _cps_low)])
check(_res_a == _res_b,
      "24a carrier identique quel que soit l'ordre des rows du fake DB")
check(_res_a[0] == _ID_HIGH and _res_a[1] == _cps_high,
      "24b départage par str(id) : l'id le plus grand l'emporte (expires_at & store égaux)")

print("-" * 64)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
