"""
test_billing_record_subscription.py — B2 : primitive idempotente
record_mobile_subscription(...) qui enregistre / réconcilie dans
mobile_subscriptions un abonnement DÉJÀ vérifié auprès du store.

100 % local : psycopg2 mocké, get_conn -> FakeConn/FakeCursor qui ne modélise
QUE les 3 requêtes SQL émises par record_mobile_subscription. Toute autre
requête (consultation_allowance, users, accounts, Stripe…) fait exploser le
fake -> preuve de non-interaction. Style aligné sur test_consultation_engine.py
/ test_auth.py (script, pas pytest).
"""

import sys
import json
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

import os
for _k, _v in {
    "SECRET_KEY": "test-secret-key",
    "VERIFY_TOKEN": "test",
    "ADMIN_PASSWORD": "test",
    "CRON_SECRET": "test",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "STRIPE_SK": "sk_test_placeholder",
    "STRIPE_WEBHOOK_SECRET": "whsec_placeholder",
    "WHATSAPP_TOKEN": "test",
    "PHONE_NUMBER_ID": "test",
    "GROQ_API_KEY": "test",
    "RESEND_API_KEY": "test",
    "META_APP_SECRET": "test",
    "DAILY_SECRET": "test",
    "SEO_SECRET": "test",
    "TAROT_MEDIA_UPLOAD_DISABLED": "1",
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


# ---------------------------------------------------------------------------
# Fausse base : UNIQUEMENT mobile_subscriptions. Aucune autre table modélisée
# -> si record_mobile_subscription touche consultation_allowance / users /
# accounts / Stripe, le fake lève AssertionError.
# ---------------------------------------------------------------------------
DB = {"mobile_subscriptions": []}
EXECUTED = []                       # historique des requêtes normalisées vues
_RACE = {"pending": None}           # ligne "insérée en concurrence" juste après notre SELECT aveugle


def reset():
    DB["mobile_subscriptions"].clear()
    EXECUTED.clear()
    _RACE["pending"] = None


def _norm(sql):
    return " ".join(sql.split())


SEL = _norm("SELECT id, user_id FROM mobile_subscriptions "
            "WHERE store=%s AND subscription_key=%s FOR UPDATE")
INS = _norm(
    "INSERT INTO mobile_subscriptions "
    "(id, user_id, store, product_id, subscription_key, latest_transaction_id, "
    "status, entitled, purchased_at, current_period_start, expires_at, auto_renewing, "
    "last_verified_at, raw_payload, created_at, updated_at) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON CONFLICT (store, subscription_key) DO NOTHING")
UPD = _norm(
    "UPDATE mobile_subscriptions SET product_id=%s, latest_transaction_id=%s, "
    "status=%s, entitled=%s, current_period_start=%s, expires_at=%s, "
    "auto_renewing=%s, last_verified_at=%s, raw_payload=%s, updated_at=%s "
    "WHERE id=%s")


class FakeCursor:
    def __init__(self):
        self._result = None
        self.rowcount = -1

    def fetchone(self):
        return self._result

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = _norm(sql)
        p = params or ()
        EXECUTED.append(k)
        self.rowcount = -1
        self._result = None

        if k == SEL:
            store, key = p
            row = next((r for r in DB["mobile_subscriptions"]
                        if r["store"] == store and r["subscription_key"] == key), None)
            pend = _RACE.get("pending")
            if row is None and pend and pend["store"] == store \
                    and pend["subscription_key"] == key:
                # une transaction concurrente vient de committer : la ligne
                # existe désormais, mais NOTRE select l'a manquée.
                _RACE["pending"] = None
                DB["mobile_subscriptions"].append(pend)
                self._result = None
                return
            self._result = (row["id"], row["user_id"]) if row else None

        elif k == INS:
            (nid, uid, store, pid, key, ltx, status, ent, pat, cps, eat, ar, lv,
             payload, cat, uat) = p
            clash = any(r for r in DB["mobile_subscriptions"]
                        if r["store"] == store and r["subscription_key"] == key)
            if clash:
                # UNIQUE(store, subscription_key) : ON CONFLICT DO NOTHING
                self.rowcount = 0
                return
            DB["mobile_subscriptions"].append({
                "id": nid, "user_id": uid, "store": store, "product_id": pid,
                "subscription_key": key, "latest_transaction_id": ltx,
                "status": status, "entitled": ent, "purchased_at": pat,
                "current_period_start": cps, "expires_at": eat,
                "auto_renewing": ar, "last_verified_at": lv,
                "raw_payload": payload, "created_at": cat, "updated_at": uat,
            })
            self.rowcount = 1

        elif k == UPD:
            (pid, ltx, status, ent, cps, eat, ar, lv, payload, uat, sid) = p
            row = next((r for r in DB["mobile_subscriptions"] if r["id"] == sid), None)
            if row is None:
                self.rowcount = 0
                return
            # purchased_at ABSENT de l'UPDATE -> jamais touché ici.
            row["product_id"] = pid
            row["latest_transaction_id"] = ltx
            row["status"] = status
            row["entitled"] = ent
            row["current_period_start"] = cps
            row["expires_at"] = eat
            row["auto_renewing"] = ar
            row["last_verified_at"] = lv
            row["raw_payload"] = payload
            row["updated_at"] = uat
            self.rowcount = 1

        else:
            raise AssertionError("SQL non modélisé par le fake B2 : " + k)


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
UID_A = "aaaaaaaa-1111-4aaa-8aaa-aaaaaaaaaaaa"
UID_B = "bbbbbbbb-2222-4bbb-8bbb-bbbbbbbbbbbb"
NOW0 = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)


def _call(**over):
    base = dict(
        user_id=UID_A, store="google_play", product_id="auryel_premium_monthly",
        subscription_key="tok-AAA", latest_transaction_id="gpa.0001",
        status="active", entitled=True, purchased_at=NOW0, current_period_start=NOW0,
        expires_at=NOW0 + timedelta(days=30),
        auto_renewing=True, raw_payload={"k": "v"}, now=NOW0,
    )
    base.update(over)
    return A.record_mobile_subscription(**base)


def _rows():
    return DB["mobile_subscriptions"]


print("-" * 64)
print("1-2. Création (google_play / app_store)")

reset()
r1 = _call()
check(r1["outcome"] == "created" and len(_rows()) == 1,
      "1 nouvel abonnement google_play -> 1 ligne créée (outcome=created)")
_row = _rows()[0]
check(_row["user_id"] == UID_A and _row["store"] == "google_play"
      and _row["product_id"] == "auryel_premium_monthly"
      and _row["subscription_key"] == "tok-AAA"
      and _row["status"] == "active"
      and _row["last_verified_at"] == NOW0
      and _row["created_at"] == NOW0 and _row["updated_at"] == NOW0,
      "1b champs de la ligne créée cohérents (last_verified_at/created_at/updated_at = now)")
check(_row["purchased_at"] == NOW0 and _row["current_period_start"] == NOW0
      and _row["expires_at"] == NOW0 + timedelta(days=30),
      "1c INSERT stocke purchased_at + current_period_start + expires_at (valeurs reçues)")

reset()
r2 = _call(store="app_store", subscription_key="orig-tx-999",
           latest_transaction_id="1000000999")
check(r2["outcome"] == "created" and len(_rows()) == 1
      and _rows()[0]["store"] == "app_store",
      "2 nouvel abonnement app_store -> 1 ligne créée")

print("-" * 64)
print("3-5. Idempotence, UPDATE évolutif & dates de période (B2.2)")

reset()
a = _call()
b = _call()                                   # exact même appel
check(len(_rows()) == 1, "3 même appel deux fois -> toujours UNE seule ligne")
check(b["outcome"] == "updated" and b["id"] == a["id"],
      "3b 2e appel identique -> outcome=updated, même id")

# --- 2. Même transaction, MÊME période revérifiée -> dates cohérentes, 1 ligne
reset()
_call()                                        # période P1 : cps = expires = NOW0(+30j)
_re = _call(now=NOW0 + timedelta(hours=2))     # revérification, mêmes dates de période
row = _rows()[0]
check(len(_rows()) == 1 and _re["outcome"] == "updated"
      and row["current_period_start"] == NOW0
      and row["expires_at"] == NOW0 + timedelta(days=30)
      and row["purchased_at"] == NOW0,
      "3c même période revérifiée -> current_period_start / expires_at / purchased_at inchangés, 1 ligne")

# --- Renouvellement : tx1/P1 -> tx2/P2
reset()
P1_START = datetime(2026, 8, 1, tzinfo=timezone.utc)
P1_END = datetime(2026, 9, 1, tzinfo=timezone.utc)
P2_START = datetime(2026, 9, 1, tzinfo=timezone.utc)
P2_END = datetime(2026, 10, 1, tzinfo=timezone.utc)
c1 = _call(purchased_at=P1_START, current_period_start=P1_START,
           expires_at=P1_END, latest_transaction_id="tx1", now=P1_START)
created_at_before = _rows()[0]["created_at"]
id_before = c1["id"]
c2 = _call(purchased_at=P2_START,               # le store fournit une nouvelle date...
           current_period_start=P2_START, expires_at=P2_END,
           latest_transaction_id="tx2", status="active",
           now=P2_START + timedelta(hours=3))
row = _rows()[0]
check(len(_rows()) == 1 and c2["outcome"] == "updated",
      "4 renouvellement -> UPDATE (toujours 1 ligne)")
check(row["current_period_start"] == P2_START,
      "4a renouvellement -> current_period_start AVANCE (2026-08-01 -> 2026-09-01)")
check(row["expires_at"] == P2_END,
      "4b renouvellement -> expires_at AVANCE (2026-09-01 -> 2026-10-01)")
check(row["purchased_at"] == P1_START,
      "5 renouvellement -> purchased_at d'ORIGINE reste 2026-08-01 (immuable après INSERT)")
check(row["latest_transaction_id"] == "tx2",
      "5a renouvellement -> latest_transaction_id = tx2")
check(row["id"] == id_before and row["created_at"] == created_at_before,
      "5b id et created_at préservés après UPDATE")

print("-" * 64)
print("6-7. Rattachement à un AUTRE compte -> refus, ligne intacte")

reset()
_call(user_id=UID_A)                           # UID_A possède tok-AAA
snap = dict(_rows()[0])
err = None
try:
    _call(user_id=UID_B)                       # UID_B tente le même (store, key)
except A.MobileSubscriptionError as e:
    err = e
check(err is not None and err.code == "account_mismatch",
      "6 autre compte + même (store, subscription_key) -> MobileSubscriptionError('account_mismatch')")
check(len(_rows()) == 1 and _rows()[0] == snap,
      "7 après refus : ligne originale strictement intacte (rien réattribué, rien modifié)")

print("-" * 64)
print("8. Même subscription_key, store différent -> autorisé")

reset()
_call(store="google_play", subscription_key="shared-key")
r8 = _call(store="app_store", subscription_key="shared-key")
check(r8["outcome"] == "created" and len(_rows()) == 2,
      "8 même subscription_key avec store différent -> 2 lignes (UNIQUE porte sur le couple)")

print("-" * 64)
print("9-10. Entrées invalides -> refus SANS écriture DB")

reset()
for _bad_store in ("stripe", "PlayStore", "", None):
    _n_before = len(_rows())
    _e = None
    try:
        _call(store=_bad_store)
    except A.MobileSubscriptionError as ex:
        _e = ex
    check(_e is not None and _e.code == "invalid_store" and len(_rows()) == _n_before,
          f"9 store invalide ({_bad_store!r}) -> 'invalid_store', 0 écriture")
check(EXECUTED == [], "9b aucune requête SQL émise pour un store invalide (refus avant get_conn)")

reset()
_e = None
try:
    _call(product_id="auryel_consultation_extra")
except A.MobileSubscriptionError as ex:
    _e = ex
check(_e is not None and _e.code == "invalid_product" and len(_rows()) == 0
      and EXECUTED == [],
      "10 product_id != auryel_premium_monthly -> 'invalid_product', 0 écriture, 0 SQL")

reset()
_e = None
try:
    _call(status="")
except A.MobileSubscriptionError as ex:
    _e = ex
check(_e is not None and _e.code == "missing_field",
      "10b status vide -> 'missing_field'")

reset()
_e = None
try:
    _call(current_period_start=None)
except A.MobileSubscriptionError as ex:
    _e = ex
check(_e is not None and _e.code == "missing_field"
      and len(_rows()) == 0 and EXECUTED == [],
      "10c current_period_start manquant -> 'missing_field', 0 écriture, 0 SQL (refus avant DB)")

print("-" * 64)
print("11. raw_payload -> JSONB (sérialisé)")

reset()
_payload = {"store": "google_play", "n": 3, "nested": {"a": [1, 2]}}
_call(raw_payload=_payload)
_stored = _rows()[0]["raw_payload"]
check(isinstance(_stored, str) and json.loads(_stored) == _payload,
      "11 raw_payload est sérialisé en JSON (stockable en JSONB) et round-trip exact")
reset()
_call(raw_payload=None)
check(_rows()[0]["raw_payload"] is None,
      "11b raw_payload=None -> NULL (pas la chaîne 'null')")

print("-" * 64)
print("12-13. Aucune interaction hors mobile_subscriptions")

reset()
_call()
_call(status="canceled")
_seen = set(EXECUTED)
check(_seen.issubset({SEL, INS, UPD}),
      "12 seules les 3 requêtes mobile_subscriptions sont émises (SELECT/INSERT/UPDATE)")
_joined = " ".join(EXECUTED).lower()
check("consultation_allowance" not in _joined and "earned_credits" not in _joined,
      "12b aucune requête ne touche consultation_allowance / earned_credits")
check(" users " not in f" {_joined} " and "stripe" not in _joined
      and "accounts" not in _joined,
      "13 aucune requête ne touche users / Stripe / accounts")

print("-" * 64)
print("14. La contrainte UNIQUE reste le filet final d'idempotence (course)")

reset()
# UID_A possède déjà la ligne, mais notre SELECT initial la "manque" ; l'INSERT
# tombe sur ON CONFLICT (rowcount 0) -> relecture FOR UPDATE -> même compte -> UPDATE.
_existing = {
    "id": "race-id-1", "user_id": UID_A, "store": "google_play",
    "product_id": "auryel_premium_monthly", "subscription_key": "tok-AAA",
    "latest_transaction_id": "gpa.old", "status": "active",
    "entitled": True,
    "purchased_at": NOW0, "current_period_start": NOW0,
    "expires_at": NOW0 + timedelta(days=30),
    "auto_renewing": True, "last_verified_at": NOW0,
    "raw_payload": None, "created_at": NOW0, "updated_at": NOW0,
}
_RACE["pending"] = _existing
r14 = _call(status="canceled", now=NOW0 + timedelta(hours=1))
check(len(_rows()) == 1 and r14["outcome"] == "updated" and r14["id"] == "race-id-1",
      "14 SELECT aveugle + ON CONFLICT DO NOTHING -> réconcilie au lieu de dupliquer (1 ligne)")
check(_rows()[0]["status"] == "canceled" and _rows()[0]["created_at"] == NOW0
      and _rows()[0]["purchased_at"] == NOW0,
      "14b la ligne concurrente est mise à jour, created_at + purchased_at préservés")

reset()
# variante : la ligne concurrente appartient à UN AUTRE compte -> refus, intacte.
# (dict neuf : _existing a pu être muté par le test 14 ci-dessus.)
_existing_other = {
    "id": "race-id-2", "user_id": UID_B, "store": "google_play",
    "product_id": "auryel_premium_monthly", "subscription_key": "tok-AAA",
    "latest_transaction_id": "gpa.old", "status": "active",
    "entitled": True,
    "purchased_at": NOW0, "current_period_start": NOW0,
    "expires_at": NOW0 + timedelta(days=30),
    "auto_renewing": True, "last_verified_at": NOW0,
    "raw_payload": None, "created_at": NOW0, "updated_at": NOW0,
}
_RACE["pending"] = _existing_other
_e = None
try:
    _call(user_id=UID_A, status="canceled")
except A.MobileSubscriptionError as ex:
    _e = ex
check(_e is not None and _e.code == "account_mismatch"
      and len(_rows()) == 1 and _rows()[0]["user_id"] == UID_B
      and _rows()[0]["status"] == "active",
      "14c course perdue au profit d'un AUTRE compte -> 'account_mismatch', ligne concurrente intacte")

print("-" * 64)
print("15. entitled — décision normalisée du vérificateur (B3-A)")

reset()
_call(entitled=True)
check(_rows()[0]["entitled"] is True,
      "15a INSERT entitled=True -> stocké True")

reset()
_call(entitled=False)
check(_rows()[0]["entitled"] is False,
      "15b INSERT entitled=False -> stocké False (droit refusé, mais ligne enregistrée)")

reset()
a = _call(entitled=True)
b = _call(entitled=False, now=NOW0 + timedelta(hours=2))
check(len(_rows()) == 1 and b["outcome"] == "updated" and b["id"] == a["id"]
      and _rows()[0]["entitled"] is False,
      "15c UPDATE true -> false (champ évolutif)")
c_ = _call(entitled=True, now=NOW0 + timedelta(hours=4))
check(len(_rows()) == 1 and c_["id"] == a["id"] and _rows()[0]["entitled"] is True,
      "15d UPDATE false -> true (champ évolutif)")

reset()
_e = None
try:
    _call(entitled=None)
except A.MobileSubscriptionError as ex:
    _e = ex
check(_e is not None and _e.code == "missing_field"
      and len(_rows()) == 0 and EXECUTED == [],
      "15e entitled=None -> 'missing_field', 0 écriture, 0 SQL (refus avant DB)")

reset()
for _bad in ("true", 1, 0, "False"):
    _n = len(_rows())
    _e = None
    try:
        _call(entitled=_bad)
    except A.MobileSubscriptionError as ex:
        _e = ex
    check(_e is not None and _e.code == "missing_field" and len(_rows()) == _n,
          f"15f entitled={_bad!r} (non booléen strict) -> 'missing_field', 0 écriture")
check(EXECUTED == [], "15g aucun SQL émis pour un entitled non booléen (refus avant get_conn)")

reset()
d1 = _call(entitled=True)
d2 = _call(entitled=True, now=NOW0 + timedelta(hours=1))
check(len(_rows()) == 1 and d2["id"] == d1["id"] and _rows()[0]["entitled"] is True,
      "15h idempotence conservée : même appel entitled=True 2x -> 1 ligne, même id")

reset()
_call(entitled=True, purchased_at=NOW0)
_call(entitled=False, purchased_at=NOW0 + timedelta(days=30),
      current_period_start=NOW0 + timedelta(days=30),
      expires_at=NOW0 + timedelta(days=60), now=NOW0 + timedelta(days=30))
_row = _rows()[0]
check(len(_rows()) == 1 and _row["purchased_at"] == NOW0
      and _row["current_period_start"] == NOW0 + timedelta(days=30)
      and _row["entitled"] is False,
      "15i purchased_at toujours immuable ; current_period_start + entitled évoluent ensemble")

print("-" * 64)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
