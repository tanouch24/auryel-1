"""
test_time_engine.py — TIMER-A.2 : moteur temps PUR et ISOLÉ.

Fake DB DÉDIÉ à ce moteur (10 statements exacts) : on NE touche PAS les
FakeCursor des suites legacy. Style script (pas pytest), aligné sur
test_consultation_engine.py.

Prouve : snapshot par bucket, débit multi-buckets dans l'ordre strict
first_free -> premium -> purchased, settle d'une fenêtre de 5 min (borné,
monotone, idempotent), touch (ouverture / prolongation / réouverture sans
facturer l'inactivité), et l'ISOLATION (aucune fonction de production ne
référence ces helpers).
"""

import sys
import inspect
import re
from datetime import datetime, timedelta, timezone
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
# Horodatages : tous le 2026-09-01, UTC (cohérent avec timedelta du moteur).
# ---------------------------------------------------------------------------
def T(h, m, s=0):
    return datetime(2026, 9, 1, h, m, s, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fake DB dédié
# ---------------------------------------------------------------------------
DB = {"accounts": {}, "allowance": [], "consultations": {}}


def reset_db():
    DB["accounts"].clear()
    DB["allowance"].clear()
    DB["consultations"].clear()


def seed_account(uid, first_free=None, purchased=0, first_consultation_used_at=None):
    DB["accounts"][str(uid)] = {
        "first_free_seconds_remaining": first_free,
        "purchased_seconds_remaining": purchased,
        "first_consultation_used_at": first_consultation_used_at,
    }


def seed_allowance(uid, period_start, period_end, allowance_seconds=28800,
                   used_seconds=0):
    DB["allowance"].append({
        "user_id": str(uid), "period_start": period_start,
        "period_end": period_end,
        "monthly_allowance_seconds": allowance_seconds,
        "monthly_used_seconds": used_seconds,
    })


def seed_consultation(cid, uid, last_activity_at=None, billed_until=None):
    DB["consultations"][str(cid)] = {
        "user_id": str(uid), "last_activity_at": last_activity_at,
        "billed_until": billed_until,
    }


def _norm(sql):
    return " ".join(sql.split())


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
        p = tuple(params or ())
        self._result = None
        self.rowcount = -1

        if k == ("SELECT first_free_seconds_remaining, purchased_seconds_remaining "
                 "FROM accounts WHERE user_id=%s") \
           or k == ("SELECT first_free_seconds_remaining, purchased_seconds_remaining "
                    "FROM accounts WHERE user_id=%s FOR UPDATE"):
            (uid,) = p
            a = DB["accounts"].get(str(uid))
            self._result = (a["first_free_seconds_remaining"],
                            a["purchased_seconds_remaining"]) if a else None

        elif k == ("SELECT monthly_allowance_seconds, monthly_used_seconds "
                   "FROM consultation_allowance WHERE user_id=%s AND period_start <= %s "
                   "AND period_end > %s ORDER BY period_start DESC LIMIT 1"):
            uid, now, now2 = p
            rows = [r for r in DB["allowance"]
                    if r["user_id"] == str(uid)
                    and r["period_start"] <= now and r["period_end"] > now2]
            rows.sort(key=lambda r: r["period_start"], reverse=True)
            if rows:
                self._result = (rows[0]["monthly_allowance_seconds"],
                                rows[0]["monthly_used_seconds"])

        elif k == ("SELECT period_start, monthly_allowance_seconds, monthly_used_seconds "
                   "FROM consultation_allowance WHERE user_id=%s AND period_start <= %s "
                   "AND period_end > %s ORDER BY period_start DESC LIMIT 1 FOR UPDATE"):
            uid, now, now2 = p
            rows = [r for r in DB["allowance"]
                    if r["user_id"] == str(uid)
                    and r["period_start"] <= now and r["period_end"] > now2]
            rows.sort(key=lambda r: r["period_start"], reverse=True)
            if rows:
                self._result = (rows[0]["period_start"],
                                rows[0]["monthly_allowance_seconds"],
                                rows[0]["monthly_used_seconds"])

        elif k == ("UPDATE accounts SET first_free_seconds_remaining=%s, "
                   "purchased_seconds_remaining=%s WHERE user_id=%s"):
            ff, pu, uid = p
            a = DB["accounts"].get(str(uid))
            if a is not None:
                a["first_free_seconds_remaining"] = ff
                a["purchased_seconds_remaining"] = pu
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k == ("UPDATE accounts SET first_consultation_used_at=%s "
                   "WHERE user_id=%s AND first_consultation_used_at IS NULL"):
            ts, uid = p
            a = DB["accounts"].get(str(uid))
            if a is not None and a.get("first_consultation_used_at") is None:
                a["first_consultation_used_at"] = ts
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k == ("UPDATE consultation_allowance SET monthly_used_seconds=%s "
                   "WHERE user_id=%s AND period_start=%s"):
            used, uid, ps = p
            r = next((x for x in DB["allowance"]
                      if x["user_id"] == str(uid) and x["period_start"] == ps), None)
            if r is not None:
                r["monthly_used_seconds"] = used
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k == ("SELECT user_id, last_activity_at, billed_until "
                   "FROM consultations WHERE id=%s"):
            (cid,) = p
            c = DB["consultations"].get(str(cid))
            self._result = (c["user_id"], c["last_activity_at"],
                            c["billed_until"]) if c else None

        elif k == "UPDATE consultations SET billed_until=%s WHERE id=%s":
            bu, cid = p
            c = DB["consultations"].get(str(cid))
            if c is not None:
                c["billed_until"] = bu
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k == ("UPDATE consultations SET last_activity_at=%s, billed_until=%s "
                   "WHERE id=%s"):
            la, bu, cid = p
            c = DB["consultations"].get(str(cid))
            if c is not None:
                c["last_activity_at"] = la
                c["billed_until"] = bu
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k == "UPDATE consultations SET last_activity_at=%s WHERE id=%s":
            la, cid = p
            c = DB["consultations"].get(str(cid))
            if c is not None:
                c["last_activity_at"] = la
                self.rowcount = 1
            else:
                self.rowcount = 0

        else:
            raise AssertionError("SQL non gere par le fake A.2 : " + k)


class FakeConn:
    def cursor(self):
        return FakeCursor()

    def commit(self):
        raise AssertionError("le moteur A.2 ne doit JAMAIS commit (curseur-in)")

    def rollback(self):
        raise AssertionError("le moteur A.2 ne doit JAMAIS rollback (curseur-in)")

    def close(self):
        raise AssertionError("le moteur A.2 ne doit JAMAIS close (curseur-in)")


def cur():
    return FakeCursor()


UID = "11111111-1111-1111-1111-111111111111"
CID = "22222222-2222-2222-2222-222222222222"
# période Premium « active » large autour des horodatages de test
PS = T(0, 0)
PE = datetime(2026, 10, 1, tzinfo=timezone.utc)

print("-" * 64)
print("Constantes / contrat")
check(A.FIRST_FREE_SECONDS == 3600, "FIRST_FREE_SECONDS = 3600")
check(A.PREMIUM_MONTHLY_SECONDS == 28800, "PREMIUM_MONTHLY_SECONDS = 28800")
check(A.ACTIVITY_GRACE_SECONDS == 300, "ACTIVITY_GRACE_SECONDS = 300")

print("-" * 64)
print("A / B / C — snapshot par bucket")

reset_db(); seed_account(UID, first_free=3600)
snap = A._get_time_snapshot_tx(cur(), UID, T(10, 0))
check(snap["first_free_remaining_seconds"] == 3600
      and snap["premium_remaining_seconds"] == 0
      and snap["purchased_remaining_seconds"] == 0
      and snap["total_remaining_seconds"] == 3600,
      "A snapshot nouveau compte -> first_free 3600, premium 0, purchased 0, total 3600")

reset_db(); seed_account(UID, first_free=3600)
seed_allowance(UID, PS, PE, allowance_seconds=28800, used_seconds=0)
snap = A._get_time_snapshot_tx(cur(), UID, T(10, 0))
check(snap["premium_remaining_seconds"] == 28800
      and snap["total_remaining_seconds"] == 3600 + 28800,
      "B snapshot Premium actif -> premium 28800")

reset_db(); seed_account(UID, first_free=0, purchased=4200)
snap = A._get_time_snapshot_tx(cur(), UID, T(10, 0))
check(snap["purchased_remaining_seconds"] == 4200,
      "C snapshot -> purchased = valeur persistee (4200)")

print("-" * 64)
print("D / E — debit simple + pas de double debit")

reset_db(); seed_account(UID, first_free=3600)
r = A._debit_consultation_seconds_tx(cur(), UID, 30, T(10, 0))
check(r["debited_seconds"] == 30
      and DB["accounts"][UID]["first_free_seconds_remaining"] == 3570
      and r["first_free_remaining_seconds"] == 3570,
      "D debit 30 s -> first_free 3600 -> 3570")

A._debit_consultation_seconds_tx(cur(), UID, 30, T(10, 1))
check(DB["accounts"][UID]["first_free_seconds_remaining"] == 3540,
      "E 2e debit 30 s -> 3540 (chaque debit retire 30, jamais 60)")

print("-" * 64)
print("F / G / T — transitions entre buckets, ordre strict")

reset_db(); seed_account(UID, first_free=10)
seed_allowance(UID, PS, PE, allowance_seconds=28800, used_seconds=0)
r = A._debit_consultation_seconds_tx(cur(), UID, 30, T(10, 0))
check(r["debited_seconds"] == 30
      and DB["accounts"][UID]["first_free_seconds_remaining"] == 0
      and DB["allowance"][0]["monthly_used_seconds"] == 20,
      "F transition first_free : 10 s ff + debit 30 -> ff 0, premium used +20")

reset_db(); seed_account(UID, first_free=0, purchased=1000)
seed_allowance(UID, PS, PE, allowance_seconds=28800, used_seconds=28790)  # premium restant 10
r = A._debit_consultation_seconds_tx(cur(), UID, 30, T(10, 0))
check(DB["allowance"][0]["monthly_used_seconds"] == 28800
      and DB["accounts"][UID]["purchased_seconds_remaining"] == 980
      and r["debited_seconds"] == 30
      and r["premium_remaining_seconds"] == 0
      and r["purchased_remaining_seconds"] == 980,
      "G transition premium -> purchased : premium 10 s + debit 30 -> premium 0, purchased -20 (1000->980)")

reset_db(); seed_account(UID, first_free=100, purchased=100)
seed_allowance(UID, PS, PE, allowance_seconds=28800, used_seconds=28700)  # premium restant 100
r = A._debit_consultation_seconds_tx(cur(), UID, 250, T(10, 0))
check(DB["accounts"][UID]["first_free_seconds_remaining"] == 0
      and DB["allowance"][0]["monthly_used_seconds"] == 28800
      and DB["accounts"][UID]["purchased_seconds_remaining"] == 50
      and r["debited_seconds"] == 250,
      "T ordre strict ff->premium->purchased : 100/100/100, debit 250 -> 0/0/50")

print("-" * 64)
print("H / I / P — buckets vides, jamais negatif")

reset_db(); seed_account(UID, first_free=0, purchased=0)  # pas d'allowance
snap = A._get_time_snapshot_tx(cur(), UID, T(10, 0))
check(snap["premium_remaining_seconds"] == 0, "H absence Premium -> premium_remaining 0")
r = A._debit_consultation_seconds_tx(cur(), UID, 30, T(10, 0))
check(r["debited_seconds"] == 0 and r["unbilled_seconds"] == 30
      and r["exhausted"] is True,
      "H2 debit sans bucket -> debited 0, unbilled 30, exhausted")

reset_db(); seed_account(UID, first_free=0, purchased=0)
r = A._debit_consultation_seconds_tx(cur(), UID, 100, T(10, 0))
check(r["total_remaining_seconds"] == 0 and r["exhausted"] is True
      and r["unbilled_seconds"] == 100,
      "I aucun bucket -> total 0, exhausted, unbilled 100")

reset_db(); seed_account(UID, first_free=5, purchased=3)
seed_allowance(UID, PS, PE, allowance_seconds=10, used_seconds=8)  # premium restant 2
r = A._debit_consultation_seconds_tx(cur(), UID, 9999, T(10, 0))
check(DB["accounts"][UID]["first_free_seconds_remaining"] == 0
      and DB["accounts"][UID]["purchased_seconds_remaining"] == 0
      and DB["allowance"][0]["monthly_used_seconds"] == 10
      and r["first_free_remaining_seconds"] == 0
      and r["premium_remaining_seconds"] == 0
      and r["purchased_remaining_seconds"] == 0
      and r["debited_seconds"] == 10,
      "P sur-debit massif -> tous buckets a 0, JAMAIS negatif, debited = 5+2+3")

print("-" * 64)
print("J / K / L / O — settle d'une fenetre")

reset_db(); seed_account(UID, first_free=3600)
seed_consultation(CID, UID, last_activity_at=T(10, 0), billed_until=T(10, 0))
s = A._settle_consultation_time_tx(cur(), UID, CID, T(10, 3))
check(s["settled_seconds"] == 180
      and DB["consultations"][CID]["billed_until"] == T(10, 3)
      and DB["accounts"][UID]["first_free_seconds_remaining"] == 3600 - 180,
      "J settle 10:00->10:03 -> 180 s facturees, billed_until = 10:03")

s = A._settle_consultation_time_tx(cur(), UID, CID, T(10, 3))
check(s["settled_seconds"] == 0
      and DB["accounts"][UID]["first_free_seconds_remaining"] == 3420,
      "K settle repetee au meme now (10:03) -> +0 s")

s = A._settle_consultation_time_tx(cur(), UID, CID, T(10, 3))
check(s["settled_seconds"] == 0, "O deux appels timestamp identique -> 0 double debit")

reset_db(); seed_account(UID, first_free=3600)
seed_consultation(CID, UID, last_activity_at=T(10, 0), billed_until=T(10, 0))
s = A._settle_consultation_time_tx(cur(), UID, CID, T(10, 8))
check(s["settled_seconds"] == 300
      and DB["consultations"][CID]["billed_until"] == T(10, 5)
      and DB["accounts"][UID]["first_free_seconds_remaining"] == 3300,
      "L settle now=10:08, last_activity 10:00 -> fenetre plafonnee a 300 s, billed_until = 10:05")

print("-" * 64)
print("M — retour tardif : l'inactivite n'est jamais facturee")

reset_db(); seed_account(UID, first_free=3600)
seed_consultation(CID, UID, last_activity_at=T(10, 0), billed_until=T(10, 0))
proc = A._process_consultation_activity_tx(cur(), UID, CID, T(10, 20))
check(proc["settled"]["settled_seconds"] == 300
      and DB["accounts"][UID]["first_free_seconds_remaining"] == 3300
      and proc["touched"]["phase"] == "reopened"
      and DB["consultations"][CID]["last_activity_at"] == T(10, 20)
      and DB["consultations"][CID]["billed_until"] == T(10, 20),
      "M retour 10:20 -> seule la traine 10:00-10:05 facturee (300 s), aucune facturation 10:05->10:20")

print("-" * 64)
print("N — message a 4 min 59 : meme fenetre, facturee jusqu'a now")

reset_db(); seed_account(UID, first_free=3600)
seed_consultation(CID, UID, last_activity_at=T(10, 0), billed_until=T(10, 0))
proc = A._process_consultation_activity_tx(cur(), UID, CID, T(10, 4, 59))
check(proc["settled"]["settled_seconds"] == 299
      and proc["touched"]["phase"] == "extended"
      and proc["touched"]["window_expires_at"] == T(10, 9, 59)
      and DB["accounts"][UID]["first_free_seconds_remaining"] == 3600 - 299,
      "N 4m59 -> ancienne fenetre facturee jusqu'a now (299 s), nouvelle expiration now+5min")

print("-" * 64)
print("touch — ouverture / prolongation")

reset_db(); seed_account(UID, first_free=3600)
seed_consultation(CID, UID)  # last_activity_at NULL
t = A._touch_consultation_activity_tx(cur(), CID, T(10, 0))
check(t["phase"] == "opened"
      and DB["consultations"][CID]["last_activity_at"] == T(10, 0)
      and DB["consultations"][CID]["billed_until"] == T(10, 0)
      and t["window_expires_at"] == T(10, 5),
      "touch 1er message -> opened, last_activity = billed_until = now")

t = A._touch_consultation_activity_tx(cur(), CID, T(10, 2))
check(t["phase"] == "extended"
      and DB["consultations"][CID]["last_activity_at"] == T(10, 2)
      and DB["consultations"][CID]["billed_until"] == T(10, 0),
      "touch pendant fenetre -> extended, last_activity avance, billed_until inchange")

print("-" * 64)
print("Q / R / S — periodes Premium")

reset_db(); seed_account(UID, first_free=0, purchased=0)
seed_allowance(UID, T(0, 0), T(8, 0), allowance_seconds=28800, used_seconds=0)  # PERIMEE avant 10:00
snap = A._get_time_snapshot_tx(cur(), UID, T(10, 0))
check(snap["premium_remaining_seconds"] == 0,
      "Q periode Premium expiree -> premium_remaining 0")
r = A._debit_consultation_seconds_tx(cur(), UID, 30, T(10, 0))
check(DB["allowance"][0]["monthly_used_seconds"] == 0 and r["debited_seconds"] == 0,
      "Q2 debit ne touche pas une periode Premium expiree")

reset_db(); seed_account(UID, first_free=0, purchased=0)
seed_allowance(UID, T(0, 0), T(8, 0), allowance_seconds=28800, used_seconds=28800)  # ancienne, epuisee
seed_allowance(UID, T(9, 0), PE, allowance_seconds=28800, used_seconds=0)           # NOUVELLE periode
r = A._debit_consultation_seconds_tx(cur(), UID, 500, T(10, 0))
_old = next(x for x in DB["allowance"] if x["period_start"] == T(0, 0))
_new = next(x for x in DB["allowance"] if x["period_start"] == T(9, 0))
check(_new["monthly_used_seconds"] == 500 and _old["monthly_used_seconds"] == 28800
      and r["debited_seconds"] == 500,
      "R nouvelle periode Premium -> debit sur monthly_used_seconds de CETTE periode, ancienne intacte")

reset_db(); seed_account(UID, first_free=0, purchased=5000)
seed_allowance(UID, T(9, 0), PE, allowance_seconds=28800, used_seconds=0)
A._debit_consultation_seconds_tx(cur(), UID, 1000, T(10, 0))
check(DB["accounts"][UID]["purchased_seconds_remaining"] == 5000
      and DB["allowance"][0]["monthly_used_seconds"] == 1000,
      "S purchased NON touche par une conso Premium (debit tenu par le bucket premium)")

print("-" * 64)
print("U / V / W — epuisement en milieu de fenetre, pas de retrofacturation")

# U. settle de 300 s avec seulement 100 s disponibles.
reset_db()
seed_account(UID, first_free=100, purchased=0)  # pas d'allowance -> premium 0
seed_consultation(CID, UID, last_activity_at=T(10, 0, 0), billed_until=T(10, 0, 0))
s = A._settle_consultation_time_tx(cur(), UID, CID, T(10, 5, 0))
check(s["settled_seconds"] == 100
      and s["requested_seconds"] == 300
      and s["debit"]["debited_seconds"] == 100
      and s["debit"]["unbilled_seconds"] == 200
      and s["exhausted"] is True
      and s["debit"]["total_remaining_seconds"] == 0
      and DB["accounts"][UID]["first_free_seconds_remaining"] == 0,
      "U settle 300 s / 100 dispo -> debite 100, unbilled 200, exhausted, aucun negatif")
check(DB["consultations"][CID]["billed_until"] == T(10, 1, 40),
      "U2 billed_until = ancien billed_until + 100 s = 10:01:40 (pas 10:05)")
check(DB["consultations"][CID]["last_activity_at"] == T(9, 56, 40),
      "U3 fenetre coupee : last_activity_at ramene a billed_until - 300 s "
      "(window_end == billed_until)")

# V. settle repete apres epuisement -> 0 nouveau debit.
s2 = A._settle_consultation_time_tx(cur(), UID, CID, T(10, 5, 0))
check(s2["settled_seconds"] == 0 and s2["debit"] is None
      and DB["accounts"][UID]["first_free_seconds_remaining"] == 0
      and DB["consultations"][CID]["billed_until"] == T(10, 1, 40),
      "V settle repete apres epuisement -> +0 s, curseur fige a 10:01:40")

s3 = A._settle_consultation_time_tx(cur(), UID, CID, T(11, 0, 0))
check(s3["settled_seconds"] == 0,
      "V2 settle bien plus tard (11:00) -> toujours +0 s (fenetre finie, aucune grace)")

# W. recharge ulterieure -> aucun rattrapage retroactif de la periode impayee.
DB["accounts"][UID]["purchased_seconds_remaining"] = 500  # l'utilisateur recharge
proc = A._process_consultation_activity_tx(cur(), UID, CID, T(10, 20, 0))
check(proc["settled"]["settled_seconds"] == 0,
      "W recharge puis message a 10:20 -> settle ne rattrape RIEN (0 s facturee)")
check(DB["accounts"][UID]["purchased_seconds_remaining"] == 500,
      "W2 le temps recharge (500) n'est PAS ampute par la periode impayee 10:01:40->10:05")
check(proc["available"] is True and proc["touched"]["phase"] == "reopened"
      and DB["consultations"][CID]["last_activity_at"] == T(10, 20, 0)
      and DB["consultations"][CID]["billed_until"] == T(10, 20, 0),
      "W3 fenetre NEUVE a now (10:20), pas de reprise de la fenetre interrompue")

# W bis. meme scenario SANS recharge -> pas de touch, fenetre reste coupee.
reset_db()
seed_account(UID, first_free=100, purchased=0)
seed_consultation(CID, UID, last_activity_at=T(10, 0, 0), billed_until=T(10, 0, 0))
A._settle_consultation_time_tx(cur(), UID, CID, T(10, 5, 0))  # epuise
proc = A._process_consultation_activity_tx(cur(), UID, CID, T(10, 20, 0))
check(proc["available"] is False and proc["touched"] is None
      and DB["consultations"][CID]["last_activity_at"] == T(9, 56, 40),
      "W4 sans recharge -> available False, aucune fenetre ouverte, coupe conservee")

print("-" * 64)
print("X / Y — convention structurelle de verrouillage (pas une preuve anti-deadlock PG)")

import types as _types

_full = inspect.getsource(A)
_es = _full.index("# MOTEUR TEMPS (TIMER-A.2)")
_ee = _full.index("# BILLING MOBILE (B2)", _es)
_ENGINE = _full[_es:_ee]

# `FROM <table> ... FOR UPDATE` DANS UN MEME LITTERAL SQL : [^"] ne franchit
# jamais une frontiere de chaine -> pas de faux positif inter-statements.
_FU_RE = re.compile(
    r'FROM\s+(accounts|consultation_allowance|consultations)\b[^"]*?FOR UPDATE')

# X. moteur : les SEULS FOR UPDATE portent sur accounts et consultation_allowance,
#    jamais sur consultations ; et le verrou accounts est pris AVANT la
#    delegation a _active_allowance_seconds_for_update (ordre d'execution).
_eng_fu = sorted(set(m.group(1).lower() for m in _FU_RE.finditer(_ENGINE)))
check(_eng_fu == ["accounts", "consultation_allowance"],
      f"X moteur : FOR UPDATE uniquement sur accounts + consultation_allowance "
      f"(trouve : {_eng_fu})")

_debit_src = inspect.getsource(A._debit_consultation_seconds_tx)
_i_acc_lock = _debit_src.find("FROM accounts WHERE user_id=%s FOR UPDATE")
_i_allow_call = _debit_src.find("_active_allowance_seconds_for_update(")
check(_i_acc_lock != -1 and _i_allow_call != -1 and _i_acc_lock < _i_allow_call,
      "X1 _debit : verrou accounts FOR UPDATE pris AVANT l'appel a "
      "_active_allowance_seconds_for_update (ordre d'execution accounts -> allowance)")

for _nm in ("_settle_consultation_time_tx", "_touch_consultation_activity_tx",
            "_consultation_time_row"):
    check(_FU_RE.search(inspect.getsource(getattr(A, _nm))) is None,
          f"X2 {_nm} ne pose AUCUN SELECT ... FOR UPDATE (lit `consultations` sans verrou)")

# Y. convention identique dans TOUT le fichier : aucun FOR UPDATE sur consultations.
_all_fu = [m.group(1).lower() for m in _FU_RE.finditer(_full)]
check("consultations" not in _all_fu,
      f"Y aucun `FROM consultations ... FOR UPDATE` dans TOUT le fichier "
      f"(trouve x{_all_fu.count('consultations')})")

# Y2 : les 2 fonctions du fichier qui verrouillent A LA FOIS accounts et
# consultation_allowance le font dans l'ordre accounts -> allowance.
#   - open_or_get_consultation : les 2 verrous DANS son corps ;
#   - resync_premium_entitlement : accounts dans son corps, allowance delegue
#     a _resync_premium_entitlement_tx (curseur-in), donc accounts d'abord.
_oog = inspect.getsource(A.open_or_get_consultation)
check("FROM accounts " in _oog and "FROM consultation_allowance" in _oog
      and _oog.index("FROM accounts ") < _oog.index("FROM consultation_allowance")
      and "FOR UPDATE" in _oog,
      "Y2a open_or_get_consultation : FROM accounts (FOR UPDATE) AVANT "
      "FROM consultation_allowance (FOR UPDATE)")
_resync = inspect.getsource(A.resync_premium_entitlement)
_resync_tx = inspect.getsource(A._resync_premium_entitlement_tx)
check(_FU_RE.search(_resync) is not None
      and _FU_RE.search(_resync).group(1).lower() == "accounts"
      and _FU_RE.search(_resync_tx) is not None
      and _FU_RE.search(_resync_tx).group(1).lower() == "consultation_allowance",
      "Y2b resync_premium_entitlement : accounts FOR UPDATE dans le wrapper, "
      "consultation_allowance FOR UPDATE seulement dans le helper curseur-in (donc apres)")

print("-" * 64)
print("A.3c-2a — first_consultation_used_at posé au 1er débit RÉEL de first_free")

# 1er débit avec take_ff > 0 -> marque posée.
reset_db(); seed_account(UID, first_free=3600, first_consultation_used_at=None)
A._debit_consultation_seconds_tx(cur(), UID, 30, T(10, 0))
check(DB["accounts"][UID]["first_consultation_used_at"] == T(10, 0)
      and DB["accounts"][UID]["first_free_seconds_remaining"] == 3570,
      "3c2a-1 débit 30 s (take_ff>0) -> first_consultation_used_at = now")

# 2e débit -> pas réécrit (idempotent, WHERE ... IS NULL).
A._debit_consultation_seconds_tx(cur(), UID, 30, T(10, 1))
check(DB["accounts"][UID]["first_consultation_used_at"] == T(10, 0),
      "3c2a-2 2e débit -> first_consultation_used_at INCHANGÉ (jamais réécrit)")

# débit qui NE touche PAS first_free (ff=0, premium seul) -> marque NON posée.
reset_db(); seed_account(UID, first_free=0, first_consultation_used_at=None)
seed_allowance(UID, PS, PE, allowance_seconds=28800, used_seconds=0)
A._debit_consultation_seconds_tx(cur(), UID, 120, T(10, 0))
check(DB["accounts"][UID]["first_consultation_used_at"] is None
      and DB["allowance"][0]["monthly_used_seconds"] == 120,
      "3c2a-3 débit premium sans take_ff -> first_consultation_used_at reste NULL")

# touch seul -> ne débite rien -> marque NON posée.
reset_db(); seed_account(UID, first_free=3600, first_consultation_used_at=None)
seed_consultation(CID, UID)
A._touch_consultation_activity_tx(cur(), CID, T(10, 0))
check(DB["accounts"][UID]["first_consultation_used_at"] is None,
      "3c2a-4 _touch seul (aucune seconde débitée) -> first_consultation_used_at reste NULL")

# settle qui débite first_free -> marque posée (même chemin _debit).
reset_db(); seed_account(UID, first_free=3600, first_consultation_used_at=None)
seed_consultation(CID, UID, last_activity_at=T(10, 0), billed_until=T(10, 0))
A._settle_consultation_time_tx(cur(), UID, CID, T(10, 2))
check(DB["accounts"][UID]["first_consultation_used_at"] == T(10, 2)
      and DB["accounts"][UID]["first_free_seconds_remaining"] == 3600 - 120,
      "3c2a-5 settle (120 s débitées sur first_free via _debit) -> marque posée")

print("-" * 64)
print("Isolation — aucune fonction de production ne reference le moteur temps")

_ENGINE_NAMES = [
    "_debit_consultation_seconds_tx", "_get_time_snapshot_tx",
    "_settle_consultation_time_tx", "_touch_consultation_activity_tx",
    "_process_consultation_activity_tx", "_active_allowance_seconds_for_update",
    "FIRST_FREE_SECONDS", "PREMIUM_MONTHLY_SECONDS", "ACTIVITY_GRACE_SECONDS",
    "first_free_seconds_remaining", "purchased_seconds_remaining",
    "monthly_allowance_seconds", "monthly_used_seconds",
    "last_activity_at", "billed_until",
]
for _fn in ("api_consultation_message", "api_consultation_state",
            "open_or_get_consultation", "get_consultation_state",
            "resync_premium_entitlement", "_resync_premium_entitlement_tx"):
    _obj = getattr(A, _fn, None)
    if _obj is None:
        check(False, f"iso {_fn} introuvable")
        continue
    _src = inspect.getsource(_obj)
    _hits = [n for n in _ENGINE_NAMES if re.search(r"\b" + re.escape(n) + r"\b", _src)]
    check(not _hits, f"iso {_fn} ne reference NI le moteur temps NI ses colonnes"
          + (f" (trouve : {_hits})" if _hits else ""))

# Le CŒUR du moteur (debit / process / active_allowance / constantes) n'apparait
# NULLE PART hors de sa section. `_settle` / `_get_time_snapshot_tx` / `_touch`
# sont appelés par le CÂBLAGE TIMER-A.3 : GET /state (A.3a :
# `_state_with_time_settle` / `api_consultation_state`) ET le helper de flux
# (A.3c-2b : `_open_time_consultation_flow_tx`) — seuls points d'appel externes
# autorisés.
_full = inspect.getsource(A)
_eng_start = _full.index("# MOTEUR TEMPS (TIMER-A.2)")
_eng_end = _full.index("# BILLING MOBILE (B2)", _eng_start)
_OUTSIDE = _full[:_eng_start] + _full[_eng_end:]
# helpers : jamais APPELÉS hors section (`nom(`) ; les docstrings du câblage A.3
# peuvent les CITER pour documenter (backtick, pas de paren) -> autorisé.
for _name in ("_debit_consultation_seconds_tx",
              "_process_consultation_activity_tx",
              "_active_allowance_seconds_for_update"):
    check(_name + "(" not in _OUTSIDE,
          f"iso {_name}() : jamais APPELÉ hors de la section MOTEUR TEMPS")
# constantes : jamais référencées du tout hors section.
for _name in ("FIRST_FREE_SECONDS", "PREMIUM_MONTHLY_SECONDS",
              "ACTIVITY_GRACE_SECONDS"):
    check(_name not in _OUTSIDE,
          f"iso {_name} : n'apparait NULLE PART hors de la section MOTEUR TEMPS")

# _settle / _get_time_snapshot_tx / _touch : hors section, UNIQUEMENT dans le
# câblage A.3 (A.3a state + A.3c-2b flow helper), jamais ailleurs.
_A3_WIRING = (inspect.getsource(A._state_with_time_settle)
              + inspect.getsource(A.api_consultation_state)
              + inspect.getsource(A._open_time_consultation_flow_tx))
for _name in ("_settle_consultation_time_tx", "_get_time_snapshot_tx",
              "_touch_consultation_activity_tx"):
    check(_name + "(" in _A3_WIRING, f"iso {_name} : bien appelé par le câblage A.3")
    _rogue = [ln.strip() for ln in _OUTSIDE.splitlines()
              if _name + "(" in ln and ln.strip() not in _A3_WIRING]
    check(not _rogue,
          f"iso {_name} : hors section MOTEUR TEMPS, appelé SEULEMENT par le "
          f"câblage A.3 (appels intrus : {_rogue})")
_RAW_ENGINE = ("_settle_consultation_time_tx", "_get_time_snapshot_tx",
               "_debit_consultation_seconds_tx", "_touch_consultation_activity_tx",
               "_process_consultation_activity_tx",
               "get_or_open_time_consultation_tx", "_open_time_consultation_tx")
# ces fonctions ne touchent JAMAIS le moteur ni le flux temps.
for _fn in ("open_or_get_consultation", "api_consultation_messages",
            "resync_premium_entitlement", "_resync_premium_entitlement_tx",
            "get_consultation_state"):
    _src = inspect.getsource(getattr(A, _fn))
    _bad = [n for n in _RAW_ENGINE + ("_open_time_consultation_flow_tx",)
            if n + "(" in _src]
    check(not _bad, f"iso {_fn} : n'appelle NI le moteur temps NI le flux A.3c "
          + (f"(trouvé : {_bad})" if _bad else ""))
# api_consultation_message (A.3c-2c) : passe UNIQUEMENT par le helper de flux
# `_open_time_consultation_flow_tx`, jamais par les primitives brutes du moteur.
_pm_src = inspect.getsource(A.api_consultation_message)
check("_open_time_consultation_flow_tx(" in _pm_src,
      "iso api_consultation_message : bascule via _open_time_consultation_flow_tx")
_pm_bad = [n for n in _RAW_ENGINE if n + "(" in _pm_src]
check(not _pm_bad,
      f"iso api_consultation_message : n'appelle AUCUNE primitive brute du moteur "
      f"(trouvé : {_pm_bad})")

print("-" * 64)
print(f"RESULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
