"""
test_billing_atomic.py — B4-D : atomicité record + resync.

Prouve que record_and_resync_mobile_subscription exécute
_record_mobile_subscription_tx + _resync_premium_entitlement_tx dans UNE SEULE
transaction DB (UNE connexion, UN commit) et que TOUT échec (record OU resync)
déclenche un ROLLBACK GLOBAL — aucune demi-écriture.

Fausse DB TRANSACTIONNELLE : chaque FakeConn travaille sur une copie profonde de
l'état committé (`DB`) ; `commit()` applique la copie, `rollback()` la jette. Un
compteur `_CONN_COUNT` prouve qu'une seule connexion est ouverte. Un
`LOCK_JOURNAL` prouve l'ordre des verrous : accounts -> mobile_subscriptions ->
consultation_allowance.

Script (pas pytest).
"""

import sys
import copy
from datetime import datetime, timezone, timedelta
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


NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
A._utcnow = lambda: NOW
PID = "auryel_premium_monthly"
UID = "11111111-1111-4111-8111-111111111111"
UID2 = "22222222-2222-4222-8222-222222222222"

# --- état COMMITTÉ ---------------------------------------------------------
DB = {"accounts": [], "mobile_subscriptions": [], "consultation_allowance": [],
      "earned_credits": [], "consultations": []}
_CONN_COUNT = [0]
LOCK_JOURNAL = []


def reset():
    for v in DB.values():
        v.clear()
    _CONN_COUNT[0] = 0
    LOCK_JOURNAL.clear()


def seed_account(uid, ff=None):
    DB["accounts"].append({"user_id": uid, "email": "u@x", "deleted_at": None,
                           "first_consultation_used_at": ff})


# --- SQL normalisé (mêmes formes que auryel_bot.py) ----------------------
MS_SEL = _n("SELECT id, user_id FROM mobile_subscriptions WHERE store=%s AND subscription_key=%s FOR UPDATE")
MS_INS = _n("INSERT INTO mobile_subscriptions (id, user_id, store, product_id, subscription_key, "
            "latest_transaction_id, status, entitled, purchased_at, current_period_start, expires_at, "
            "auto_renewing, last_verified_at, raw_payload, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (store, subscription_key) DO NOTHING")
MS_UPD = _n("UPDATE mobile_subscriptions SET product_id=%s, latest_transaction_id=%s, status=%s, "
            "entitled=%s, current_period_start=%s, expires_at=%s, auto_renewing=%s, "
            "last_verified_at=%s, raw_payload=%s, updated_at=%s WHERE id=%s")

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

GS_CONS = _n("SELECT id, advisor_id, started_at, expires_at, credit_source FROM consultations "
             "WHERE user_id=%s AND expires_at > %s ORDER BY started_at DESC LIMIT 1")
GS_ALW = _n("SELECT period_start, period_end, monthly_limit, monthly_used FROM consultation_allowance "
            "WHERE user_id=%s AND period_start <= %s AND period_end > %s ORDER BY period_start DESC LIMIT 1")
GS_EARNED = _n("SELECT COUNT(*) FROM earned_credits WHERE user_id=%s AND consumed_at IS NULL")
GS_FF = _n("SELECT first_consultation_used_at FROM accounts WHERE user_id=%s")


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
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
        w = self.conn.work                       # copie de travail (non committée)
        k = _n(sql)
        p = params or ()
        self.rowcount = -1
        self._r = None
        self._rows = None

        if "FOR UPDATE" in k:
            if k == R_ACC:
                LOCK_JOURNAL.append("accounts")
            elif k == MS_SEL:
                LOCK_JOURNAL.append("mobile_subscriptions")
            elif k == R_ALW:
                LOCK_JOURNAL.append("consultation_allowance")

        if k == R_ACC:
            (uid,) = p
            uid = str(uid)
            r = next((a for a in w["accounts"]
                      if a["user_id"] == uid and a["deleted_at"] is None), None)
            self._r = (uid,) if r else None
        elif k == MS_SEL:
            store, key = p
            row = next((r for r in w["mobile_subscriptions"]
                        if r["store"] == store and r["subscription_key"] == key), None)
            self._r = (row["id"], row["user_id"]) if row else None
        elif k == MS_INS:
            (nid, uid, store, pid, key, ltx, st, ent, pat, cps, eat, ar, lv, pay, cat, uat) = p
            if any(r for r in w["mobile_subscriptions"]
                   if r["store"] == store and r["subscription_key"] == key):
                self.rowcount = 0
            else:
                w["mobile_subscriptions"].append(dict(
                    id=nid, user_id=uid, store=store, product_id=pid, subscription_key=key,
                    latest_transaction_id=ltx, status=st, entitled=ent, purchased_at=pat,
                    current_period_start=cps, expires_at=eat, auto_renewing=ar,
                    last_verified_at=lv, raw_payload=pay, created_at=cat, updated_at=uat))
                self.rowcount = 1
        elif k == MS_UPD:
            (pid, ltx, st, ent, cps, eat, ar, lv, pay, uat, sid) = p
            row = next((r for r in w["mobile_subscriptions"] if r["id"] == sid), None)
            if row:
                row.update(product_id=pid, latest_transaction_id=ltx, status=st, entitled=ent,
                           current_period_start=cps, expires_at=eat, auto_renewing=ar,
                           last_verified_at=lv, raw_payload=pay, updated_at=uat)
                self.rowcount = 1
            else:
                self.rowcount = 0
        elif k == R_SUBS:
            (uid,) = p
            uid = str(uid)
            self._rows = [(s["id"], s["current_period_start"], s["expires_at"],
                           s["entitled"], s["store"])
                          for s in w["mobile_subscriptions"] if s["user_id"] == uid]
        elif k == R_ALW:
            (uid,) = p
            uid = str(uid)
            self._rows = [(a["period_start"], a["period_end"], a["monthly_limit"],
                           a["monthly_used"], a["source_subscription_id"], a["source_period_start"])
                          for a in w["consultation_allowance"] if a["user_id"] == uid]
        elif k == R_UPE:
            pe, uid, ps = p
            uid = str(uid)
            n = 0
            for a in w["consultation_allowance"]:
                if a["user_id"] == uid and a["period_start"] == ps:
                    a["period_end"] = pe
                    n += 1
            self.rowcount = n
        elif k == R_UPE_SRC:
            pe, src, sps, uid, ps = p
            uid = str(uid)
            n = 0
            for a in w["consultation_allowance"]:
                if a["user_id"] == uid and a["period_start"] == ps:
                    a.update(period_end=pe, source_subscription_id=src, source_period_start=sps)
                    n += 1
            self.rowcount = n
        elif k == R_DEL_ALL:
            uid, now = p
            uid = str(uid)
            before = len(w["consultation_allowance"])
            w["consultation_allowance"][:] = [
                a for a in w["consultation_allowance"]
                if not (a["user_id"] == uid and a["period_start"] > now)]
            self.rowcount = before - len(w["consultation_allowance"])
        elif k == R_DEL_NC:
            uid, now, cid, ccps = p
            uid = str(uid)
            before = len(w["consultation_allowance"])
            w["consultation_allowance"][:] = [
                a for a in w["consultation_allowance"]
                if not (a["user_id"] == uid and a["period_start"] > now
                        and (_distinct(a["source_subscription_id"], cid)
                             or _distinct(a["source_period_start"], ccps)))]
            self.rowcount = before - len(w["consultation_allowance"])
        elif k == INS_ALW:
            uid, ps, pe, lim, created, src, sps = p
            uid = str(uid)
            if any(a for a in w["consultation_allowance"]
                   if a["user_id"] == uid and a["period_start"] == ps):
                self.rowcount = 0
            else:
                w["consultation_allowance"].append(dict(
                    user_id=uid, period_start=ps, period_end=pe, monthly_limit=lim,
                    monthly_used=0, created_at=created, source_subscription_id=src,
                    source_period_start=sps))
                self.rowcount = 1
        elif k == GS_CONS:
            self._r = None
        elif k == GS_ALW:
            uid, now, _n2 = p
            uid = str(uid)
            rows = sorted([a for a in w["consultation_allowance"]
                           if a["user_id"] == uid and a["period_start"] <= now and a["period_end"] > now],
                          key=lambda a: a["period_start"], reverse=True)
            if rows:
                a = rows[0]
                self._r = (a["period_start"], a["period_end"], a["monthly_limit"], a["monthly_used"])
        elif k == GS_EARNED:
            (uid,) = p
            uid = str(uid)
            self._r = (sum(1 for e in w["earned_credits"]
                           if e["user_id"] == uid and e["consumed_at"] is None),)
        elif k == GS_FF:
            (uid,) = p
            uid = str(uid)
            r = next((a for a in w["accounts"] if a["user_id"] == str(uid)), None)
            self._r = (r["first_consultation_used_at"],) if r is not None else None
        else:
            raise AssertionError("SQL non modélisé (atomic B4-D) : " + k)


class FakeConn:
    """Connexion TRANSACTIONNELLE : travaille sur une copie profonde de l'état
    committé. commit() applique la copie à DB ; rollback() la jette."""

    def __init__(self):
        self.work = copy.deepcopy(DB)
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        for t in DB:
            DB[t][:] = [copy.deepcopy(r) for r in self.work[t]]
        self.committed = True

    def rollback(self):
        self.work = copy.deepcopy(DB)
        self.rolled_back = True

    def close(self):
        pass


def _counting_get_conn():
    _CONN_COUNT[0] += 1
    return FakeConn()


A.get_conn = _counting_get_conn

CPS0 = NOW - timedelta(days=2)
EXP0 = NOW + timedelta(days=28)


def norm(store="app_store", key="sub-1", entitled=True, status="active",
         cps=None, expires=None, purchased=None, ltx="tx-1"):
    return dict(
        user_id=UID, store=store, product_id=PID, subscription_key=key,
        latest_transaction_id=ltx, status=status, entitled=entitled,
        purchased_at=purchased or (NOW - timedelta(days=40)),
        current_period_start=cps or CPS0, expires_at=expires or EXP0,
        auto_renewing=True, raw_payload={"source": store}, now=NOW,
    )


def _subs(uid=UID):
    return [s for s in DB["mobile_subscriptions"] if s["user_id"] == uid]


def _alw(uid=UID):
    return [a for a in DB["consultation_allowance"] if a["user_id"] == uid]


def _active(uid=UID, now=NOW):
    return [a for a in _alw(uid) if a["period_start"] <= now < a["period_end"]]


print("=" * 64)
print("TEST BILLING ATOMIC — record + resync dans UNE transaction")
print("=" * 64)

# --- A. succès normal ---------------------------------------------------
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
_CONN_COUNT[0] = 0
res = A.record_and_resync_mobile_subscription(**norm(key="a-1"))
check(_CONN_COUNT[0] == 1, "A1 record_and_resync ouvre EXACTEMENT 1 connexion")
check(res["subscription"]["outcome"] == "created"
      and res["resync"]["premium"] is True,
      "A2 retour {subscription, resync} cohérent")
check(len(_subs()) == 1 and _subs()[0]["entitled"] is True,
      "A3 subscription committée (entitled True)")
check(len(_active()) == 1 and _active()[0]["monthly_limit"] == 4
      and _active()[0]["monthly_used"] == 0,
      "A4 allowance active committée (limit 4, used 0)")

# --- ordre des verrous (invariant) -----------------------------------
check(LOCK_JOURNAL == ["accounts", "mobile_subscriptions", "consultation_allowance"],
      "A5 ordre des verrous : accounts -> mobile_subscriptions -> consultation_allowance")

# --- B. erreur pendant record -> rollback global ---------------------
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
err = None
try:
    A.record_and_resync_mobile_subscription(**norm(key="b-1", entitled="oui"))  # pas un bool
except A.MobileSubscriptionError as e:
    err = e
check(err is not None and err.code == "missing_field",
      "B1 entitled non booléen -> MobileSubscriptionError('missing_field')")
check(len(_subs()) == 0 and len(_alw()) == 0,
      "B2 rollback global : aucune subscription, aucune allowance")

# --- C. account_mismatch -> rollback global, état existant intact ----
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
seed_account(UID2, ff=NOW - timedelta(days=5))
A.record_and_resync_mobile_subscription(**norm(key="shared"))          # UID possède 'shared'
snap_subs = copy.deepcopy(DB["mobile_subscriptions"])
snap_alw = copy.deepcopy(DB["consultation_allowance"])
n2 = norm(key="shared")
n2["user_id"] = UID2
err = None
try:
    A.record_and_resync_mobile_subscription(**n2)
except A.MobileSubscriptionError as e:
    err = e
check(err is not None and err.code == "account_mismatch",
      "C1 (store, subscription_key) d'un autre compte -> account_mismatch")
check(DB["mobile_subscriptions"] == snap_subs and DB["consultation_allowance"] == snap_alw,
      "C2 rollback global : subscription existante inchangée, aucune allowance UID2")
check(len(_alw(UID2)) == 0, "C3 aucune allowance créée pour l'autre compte")

# --- D. erreur pendant resync APRÈS INSERT subscription (CRITIQUE) ---
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
_orig_resync_tx = A._resync_premium_entitlement_tx


def _boom_after_writes(cursor, user_id, now):
    _orig_resync_tx(cursor, user_id, now)        # exécute vraiment les écritures quota
    raise RuntimeError("resync boom APRÈS écritures")


A._resync_premium_entitlement_tx = _boom_after_writes
err = None
try:
    A.record_and_resync_mobile_subscription(**norm(key="d-1"))
except RuntimeError as e:
    err = e
finally:
    A._resync_premium_entitlement_tx = _orig_resync_tx
check(err is not None,
      "D1 exception resync après écritures -> propagée")
check(len(_subs()) == 0,
      "D2 ROLLBACK GLOBAL : la subscription nouvellement INSÉRÉE a DISPARU")
check(len(_alw()) == 0,
      "D3 ROLLBACK GLOBAL : aucune allowance committée")

# --- E. erreur pendant resync APRÈS UPDATE d'une subscription existante --
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
A.record_and_resync_mobile_subscription(**norm(key="e-1", status="active",
                                               expires=EXP0))
before_row = copy.deepcopy(_subs()[0])
before_alw = copy.deepcopy(_alw())
A._resync_premium_entitlement_tx = _boom_after_writes
err = None
try:
    # 2e verify : passe status/expires différents -> UPDATE de la ligne existante,
    # puis resync explose.
    A.record_and_resync_mobile_subscription(**norm(key="e-1", status="canceled",
                                                   expires=EXP0 + timedelta(days=10)))
except RuntimeError as e:
    err = e
finally:
    A._resync_premium_entitlement_tx = _orig_resync_tx
check(err is not None, "E1 exception resync après UPDATE -> propagée")
check(_subs()[0] == before_row,
      "E2 ROLLBACK : les anciennes valeurs de mobile_subscriptions sont restaurées "
      "(status/expires/updated_at d'avant)")
check(_alw() == before_alw,
      "E3 ROLLBACK : consultation_allowance inchangée")

# --- F. replay -> idempotent, jamais quota 8 ------------------------
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
for _i in range(6):
    A.record_and_resync_mobile_subscription(**norm(key="f-1", cps=CPS0, expires=EXP0))
check(len(_subs()) == 1 and len(_alw()) == 1 and len(_active()) == 1
      and _active()[0]["monthly_used"] == 0,
      "F 6 appels successifs -> 1 subscription, 1 allowance, used 0 (idempotent)")

# --- G. renouvellement -> future allowance dans la même transaction --
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
A.record_and_resync_mobile_subscription(**norm(key="g-1", cps=CPS0, expires=EXP0))
_active()[0]["monthly_used"] = 3
cyc2 = EXP0
A.record_and_resync_mobile_subscription(**norm(key="g-1", cps=cyc2,
                                               expires=cyc2 + timedelta(days=30)))
fut = [a for a in _alw() if a["period_start"] == cyc2]
check(len(fut) == 1 and fut[0]["monthly_used"] == 0
      and len(_active()) == 1 and _active()[0]["monthly_used"] == 3,
      "G renouvellement anticipé -> future allowance (used 0) créée, active courante (used 3) intacte")

# --- H. first_free jamais touchée ----------------------------------
reset()
seed_account(UID, ff=None)
A.record_and_resync_mobile_subscription(**norm(key="h-1"))
acc = next(a for a in DB["accounts"] if a["user_id"] == UID)
check(acc["first_consultation_used_at"] is None,
      "H first_consultation_used_at jamais écrite par le chemin atomique")
A.record_and_resync_mobile_subscription(**norm(key="h-1"))
acc = next(a for a in DB["accounts"] if a["user_id"] == UID)
check(acc["first_consultation_used_at"] is None, "H2 re-verify : toujours None")

# --- I. earned_credits jamais touchés ----------------------------
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
DB["earned_credits"].append({"user_id": UID, "source": "referral",
                             "granted_at": NOW - timedelta(days=1),
                             "consumed_at": None, "consultation_id": None, "metadata": None})
DB["earned_credits"].append({"user_id": UID, "source": "share_30d",
                             "granted_at": NOW - timedelta(days=1),
                             "consumed_at": None, "consultation_id": None, "metadata": None})
A.record_and_resync_mobile_subscription(**norm(key="i-1"))
A.record_and_resync_mobile_subscription(**norm(key="i-1", status="expired", entitled=False))
A.record_and_resync_mobile_subscription(**norm(key="i-1"))
n_unused = len([e for e in DB["earned_credits"] if e["consumed_at"] is None])
check(n_unused == 2, "I 2 earned credits non consommés, jamais touchés par verify/résync/révocation")

# --- J. monthly_used jamais remis à zéro (reverify + bascule) --------
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
A.record_and_resync_mobile_subscription(**norm(store="google_play", key="j-g",
                                               cps=CPS0, expires=EXP0))
_active()[0]["monthly_used"] = 4                       # quota épuisé
# bascule de porteuse : Apple prend la main sur le MÊME cycle (expiry plus loin)
A.record_and_resync_mobile_subscription(**norm(store="app_store", key="j-a",
                                               cps=CPS0, expires=EXP0 + timedelta(days=5)))
check(len(_active()) == 1 and _active()[0]["monthly_used"] == 4
      and _active()[0]["source_subscription_id"] is not None,
      "J bascule de porteuse sur le même cycle -> re-pointée, monthly_used=4 CONSERVÉ")
# reverify simple -> toujours 4
A.record_and_resync_mobile_subscription(**norm(store="app_store", key="j-a",
                                               cps=CPS0, expires=EXP0 + timedelta(days=5)))
check(_active()[0]["monthly_used"] == 4, "J2 reverify -> monthly_used=4 toujours conservé")

# --- K. les helpers tx n'ouvrent JAMAIS de connexion ----------------
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
_probe = FakeConn()
_cur = _probe.cursor()
_CONN_COUNT[0] = 0
A._record_mobile_subscription_tx(_cur, UID, "app_store", PID, "k-1", "tx", "active",
                                 True, NOW - timedelta(days=40), CPS0, EXP0, True,
                                 {"s": "x"}, now=NOW)
A._resync_premium_entitlement_tx(_cur, UID, NOW)
A._insert_allowance(_cur, UID, CPS0, EXP0, 4, NOW)
check(_CONN_COUNT[0] == 0,
      "K _record_mobile_subscription_tx / _resync_premium_entitlement_tx / _insert_allowance "
      "n'appellent JAMAIS get_conn")

# --- L. wrappers publics : 1 connexion, contrat inchangé ------------
reset()
seed_account(UID, ff=NOW - timedelta(days=5))
_CONN_COUNT[0] = 0
r_rec = A.record_mobile_subscription(
    user_id=UID, store="app_store", product_id=PID, subscription_key="l-1",
    latest_transaction_id="tx", status="active", entitled=True,
    purchased_at=NOW - timedelta(days=40), current_period_start=CPS0,
    expires_at=EXP0, auto_renewing=True, raw_payload={"s": "x"}, now=NOW)
c1 = _CONN_COUNT[0]
r_res = A.resync_premium_entitlement(UID, now=NOW)
c2 = _CONN_COUNT[0]
check(c1 == 1 and c2 == 2 and r_rec["outcome"] == "created"
      and r_res["premium"] is True,
      "L record_mobile_subscription + resync_premium_entitlement : 1 connexion chacun, contrat public identique")

reset()
r_unknown = A.resync_premium_entitlement("00000000-0000-4000-8000-000000000000", now=NOW)
check(r_unknown["action"] == "unknown_account" and r_unknown["premium"] is False,
      "L2 resync compte absent -> unknown_account (contrat public inchangé)")

# --- M. account absent sur le chemin atomique ----------------------
reset()
err = None
try:
    A.record_and_resync_mobile_subscription(**norm(key="m-1"))
except A.MobileSubscriptionError as e:
    err = e
check(err is not None and err.code == "account_mismatch"
      and len(DB["mobile_subscriptions"]) == 0 and len(DB["consultation_allowance"]) == 0,
      "M compte inconnu -> MobileSubscriptionError, rollback, rien écrit")

print("-" * 64)
print(f"RÉSULTAT : {_S['pass']} ok / {_S['fail']} ko")
sys.exit(1 if _S["fail"] else 0)
