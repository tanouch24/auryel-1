"""
test_time_consultation_flow.py — TIMER-A.3c-2b : helper transactionnel
`_open_time_consultation_flow_tx` (settle -> snapshot -> 402 -> open logique ->
touch -> attach tirage -> commit), SANS LLM et NON câblé dans
api_consultation_message.

Fake DB DÉDIÉ (accounts / consultation_allowance / consultations / tirages) avec
une vraie FakeConn commit/rollback (le helper POSSÈDE la connexion). Style
script, aligné sur test_time_engine.py.
"""

import sys
import ast
import copy
import inspect
import re
import textwrap
import types
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


def T(h, m, s=0):
    return datetime(2026, 9, 1, h, m, s, tzinfo=timezone.utc)


UID = "11111111-1111-1111-1111-111111111111"
OTHER = "99999999-9999-9999-9999-999999999999"
PS = T(0, 0)
PE = datetime(2026, 10, 1, tzinfo=timezone.utc)

DB = {"accounts": {}, "allowance": [], "consultations": {}, "tirages": {},
      "earned": []}
_COMMITS = {"n": 0}
_ROLLBACKS = {"n": 0}


def reset_db():
    DB["accounts"].clear()
    DB["allowance"].clear()
    DB["consultations"].clear()
    DB["tirages"].clear()
    DB["earned"].clear()
    _COMMITS["n"] = 0
    _ROLLBACKS["n"] = 0


def seed_earned(n=1, uid=UID):
    for i in range(n):
        DB["earned"].append({"id": f"e-{i}", "user_id": str(uid),
                             "consumed_at": None})


def seed_account(uid=UID, deleted_at=None, first_free=3600, purchased=0,
                 first_consultation_used_at=None):
    DB["accounts"][str(uid)] = {
        "user_id": str(uid), "deleted_at": deleted_at,
        "first_free_seconds_remaining": first_free,
        "purchased_seconds_remaining": purchased,
        "first_consultation_used_at": first_consultation_used_at,
    }


def seed_allowance(uid=UID, allowance_seconds=28800, used_seconds=0,
                   period_start=PS, period_end=PE):
    DB["allowance"].append({
        "user_id": str(uid), "period_start": period_start, "period_end": period_end,
        "monthly_allowance_seconds": allowance_seconds,
        "monthly_used_seconds": used_seconds,
    })


def seed_consultation(cid, advisor_id, started_at, last_activity_at=None,
                      billed_until=None, uid=UID):
    DB["consultations"][str(cid)] = {
        "id": str(cid), "user_id": str(uid), "advisor_id": advisor_id,
        "started_at": started_at, "expires_at": started_at + timedelta(hours=2),
        "credit_source": "monthly", "created_at": started_at,
        "last_activity_at": last_activity_at, "billed_until": billed_until,
    }


def seed_tirage(tid, uid=UID, consultation_id=None):
    DB["tirages"][str(tid)] = {
        "id": str(tid), "user_id": str(uid), "card_keys": ["le_fou"],
        "advisor_id": None, "consultation_id": consultation_id,
        "created_at": T(9, 0),
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

        if k == "SELECT id FROM tirages WHERE id=%s AND user_id=%s":
            tid, uid = p
            t = DB["tirages"].get(str(tid))
            self._result = (t["id"],) if t and t["user_id"] == str(uid) else None

        elif k == ("SELECT user_id FROM accounts "
                   "WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE"):
            (uid,) = p
            a = DB["accounts"].get(str(uid))
            self._result = (a["user_id"],) if a and a["deleted_at"] is None else None

        elif k == ("SELECT id, last_activity_at FROM consultations "
                   "WHERE user_id=%s ORDER BY started_at DESC LIMIT 1"):
            (uid,) = p
            rows = [c for c in DB["consultations"].values()
                    if c["user_id"] == str(uid)]
            rows.sort(key=lambda c: c["started_at"], reverse=True)
            if rows:
                self._result = (rows[0]["id"], rows[0]["last_activity_at"])

        elif k == ("SELECT id, advisor_id, last_activity_at, billed_until "
                   "FROM consultations WHERE user_id=%s "
                   "ORDER BY started_at DESC LIMIT 1"):
            (uid,) = p
            rows = [c for c in DB["consultations"].values()
                    if c["user_id"] == str(uid)]
            rows.sort(key=lambda c: c["started_at"], reverse=True)
            if rows:
                c0 = rows[0]
                self._result = (c0["id"], c0["advisor_id"],
                                c0["last_activity_at"], c0["billed_until"])

        elif k == ("SELECT user_id, last_activity_at, billed_until "
                   "FROM consultations WHERE id=%s"):
            (cid,) = p
            c0 = DB["consultations"].get(str(cid))
            self._result = ((c0["user_id"], c0["last_activity_at"],
                             c0["billed_until"]) if c0 else None)

        elif k == "UPDATE consultations SET billed_until=%s WHERE id=%s":
            bu, cid = p
            c0 = DB["consultations"].get(str(cid))
            if c0:
                c0["billed_until"] = bu
                self.rowcount = 1

        elif k == ("UPDATE consultations SET last_activity_at=%s, billed_until=%s "
                   "WHERE id=%s"):
            la, bu, cid = p
            c0 = DB["consultations"].get(str(cid))
            if c0:
                c0["last_activity_at"] = la
                c0["billed_until"] = bu
                self.rowcount = 1

        elif k == "UPDATE consultations SET last_activity_at=%s WHERE id=%s":
            la, cid = p
            c0 = DB["consultations"].get(str(cid))
            if c0:
                c0["last_activity_at"] = la
                self.rowcount = 1

        elif k in ("SELECT first_free_seconds_remaining, purchased_seconds_remaining "
                   "FROM accounts WHERE user_id=%s",
                   "SELECT first_free_seconds_remaining, purchased_seconds_remaining "
                   "FROM accounts WHERE user_id=%s FOR UPDATE"):
            (uid,) = p
            a = DB["accounts"].get(str(uid))
            self._result = ((a["first_free_seconds_remaining"],
                             a["purchased_seconds_remaining"]) if a else None)

        elif k in ("SELECT monthly_allowance_seconds, monthly_used_seconds "
                   "FROM consultation_allowance WHERE user_id=%s AND period_start <= %s "
                   "AND period_end > %s ORDER BY period_start DESC LIMIT 1",
                   "SELECT period_start, monthly_allowance_seconds, monthly_used_seconds "
                   "FROM consultation_allowance WHERE user_id=%s AND period_start <= %s "
                   "AND period_end > %s ORDER BY period_start DESC LIMIT 1 FOR UPDATE"):
            uid, now, _n2 = p
            rows = [a for a in DB["allowance"]
                    if a["user_id"] == str(uid)
                    and a["period_start"] <= now and a["period_end"] > now]
            rows.sort(key=lambda a: a["period_start"], reverse=True)
            if rows:
                a0 = rows[0]
                if k.startswith("SELECT period_start,"):
                    self._result = (a0["period_start"],
                                    a0["monthly_allowance_seconds"],
                                    a0["monthly_used_seconds"])
                else:
                    self._result = (a0["monthly_allowance_seconds"],
                                    a0["monthly_used_seconds"])

        elif k == ("UPDATE accounts SET first_free_seconds_remaining=%s, "
                   "purchased_seconds_remaining=%s WHERE user_id=%s"):
            ff, pu, uid = p
            a = DB["accounts"].get(str(uid))
            if a:
                a["first_free_seconds_remaining"] = ff
                a["purchased_seconds_remaining"] = pu
                self.rowcount = 1

        elif k == ("UPDATE accounts SET first_consultation_used_at=%s "
                   "WHERE user_id=%s AND first_consultation_used_at IS NULL"):
            ts, uid = p
            a = DB["accounts"].get(str(uid))
            if a and a.get("first_consultation_used_at") is None:
                a["first_consultation_used_at"] = ts
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k == ("UPDATE consultation_allowance SET monthly_used_seconds=%s "
                   "WHERE user_id=%s AND period_start=%s"):
            used, uid, ps = p
            a = next((x for x in DB["allowance"]
                      if x["user_id"] == str(uid) and x["period_start"] == ps), None)
            if a:
                a["monthly_used_seconds"] = used
                self.rowcount = 1

        elif k == ("INSERT INTO consultations (id, user_id, advisor_id, started_at, "
                   "expires_at, credit_source, created_at) "
                   "VALUES (%s, %s, %s, %s, %s, 'time', %s)"):
            cid, uid, advisor, started, expires, created = p
            DB["consultations"][str(cid)] = {
                "id": str(cid), "user_id": str(uid), "advisor_id": advisor,
                "started_at": started, "expires_at": expires,
                "credit_source": "time", "created_at": created,
                "last_activity_at": None, "billed_until": None,
            }
            self.rowcount = 1

        elif k == ("UPDATE tirages SET consultation_id=%s "
                   "WHERE id=%s AND user_id=%s AND consultation_id IS NULL"):
            cid, tid, uid = p
            t = DB["tirages"].get(str(tid))
            if (t and t["user_id"] == str(uid) and t["consultation_id"] is None):
                t["consultation_id"] = str(cid)
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k == ("SELECT COUNT(*) FROM earned_credits "
                   "WHERE user_id=%s AND consumed_at IS NULL"):
            (uid,) = p
            n = sum(1 for e in DB["earned"]
                    if e["user_id"] == str(uid) and e["consumed_at"] is None)
            self._result = (n,)

        elif k == ("SELECT period_start, period_end FROM consultation_allowance "
                   "WHERE user_id=%s AND period_start <= %s AND period_end > %s "
                   "ORDER BY period_start DESC LIMIT 1"):
            uid, now, _n2 = p
            rows = [a for a in DB["allowance"]
                    if a["user_id"] == str(uid)
                    and a["period_start"] <= now and a["period_end"] > now]
            rows.sort(key=lambda a: a["period_start"], reverse=True)
            if rows:
                self._result = (rows[0]["period_start"], rows[0]["period_end"])

        else:
            raise AssertionError("SQL non géré par le fake A.3c-2b : " + k)


class FakeConn:
    def __init__(self):
        self._snap = copy.deepcopy(DB)

    def cursor(self):
        return FakeCursor()

    def commit(self):
        _COMMITS["n"] += 1
        self._snap = copy.deepcopy(DB)

    def rollback(self):
        _ROLLBACKS["n"] += 1
        for k in DB:
            DB[k].clear()
            DB[k].update(self._snap[k]) if isinstance(DB[k], dict) else \
                DB[k].extend(self._snap[k])

    def close(self):
        pass


A.get_conn = lambda: FakeConn()


def flow(preferred, now, tirage_id=None):
    return A._open_time_consultation_flow_tx(UID, preferred, tirage_id, now)


def _acc():
    return DB["accounts"][UID]


def _consultations():
    return list(DB["consultations"].values())


# ---------------------------------------------------------------------------
print("-" * 64)
print("A — premier message : consultation time créée, aucun débit, aucun LLM")
reset_db(); seed_account(first_free=3600)
r = flow("selena", T(10, 0))
check(r["status"] == "ok" and r["consultation"]["created"] is True
      and r["consultation"]["advisor_id"] == "selena"
      and len(_consultations()) == 1, "A status ok, consultation time créée (advisor=selena)")
_c = _consultations()[0]
check(_c["credit_source"] == "time"
      and _c["last_activity_at"] == T(10, 0) and _c["billed_until"] == T(10, 0),
      "A2 credit_source='time', touch -> last_activity=billed_until=10:00")
check(_acc()["first_free_seconds_remaining"] == 3600
      and _acc()["first_consultation_used_at"] is None,
      "A3 first_free reste 3600, first_consultation_used_at reste NULL (0 s débitée)")
check(r["time"]["total_remaining_seconds"] == 3600
      and r["time"]["window_active"] is True
      and r["time"]["window_expires_at"] == T(10, 5),
      "A4 time.total 3600, window_active true, window_expires_at = 10:05")
check(_COMMITS["n"] == 1, "A5 exactement 1 commit")

print("-" * 64)
print("B/C — fenêtre active 4m59 + conseiller préféré changé -> settle 299, même consultation")
reset_db(); seed_account(first_free=3600)
seed_consultation("c-1", "selena", T(9, 0), last_activity_at=T(10, 0), billed_until=T(10, 0))
r = flow("ezra", T(10, 4, 59))
check(r["status"] == "ok" and r["consultation"]["created"] is False
      and r["consultation"]["consultation_id" if "consultation_id" in r["consultation"] else "id"] == "c-1"
      and r["consultation"]["advisor_id"] == "selena"
      and len(_consultations()) == 1,
      "B/C fenêtre active + preferred=ezra -> reprise c-1, advisor=selena (figé)")
check(_acc()["first_free_seconds_remaining"] == 3600 - 299,
      "B2 settle 299 s -> first_free = 3301")
check(_acc()["first_consultation_used_at"] == T(10, 4, 59),
      "B3 first_consultation_used_at posé au 1er débit réel (via _debit)")
check(DB["consultations"]["c-1"]["last_activity_at"] == T(10, 4, 59)
      and r["time"]["window_expires_at"] == T(10, 9, 59),
      "B4 touch 10:04:59 -> nouvelle fenêtre jusqu'à 10:09:59")

print("-" * 64)
print("D — retour >5 min même conseiller -> settle ≤300, reprise, aucune facturation du gap")
reset_db(); seed_account(first_free=3600)
seed_consultation("c-1", "selena", T(9, 0), last_activity_at=T(10, 0), billed_until=T(10, 0))
r = flow("selena", T(10, 20))
check(r["status"] == "ok" and r["consultation"]["created"] is False,
      "D même conseiller après >5 min -> reprise (created False)")
check(_acc()["first_free_seconds_remaining"] == 3600 - 300,
      "D2 settle plafonné à 300 s (traîne), rien facturé sur 10:05->10:20")
check(len(_consultations()) == 1
      and DB["consultations"]["c-1"]["last_activity_at"] == T(10, 20)
      and DB["consultations"]["c-1"]["billed_until"] == T(10, 20),
      "D3 même consultation, nouvelle fenêtre à 10:20")

print("-" * 64)
print("E — retour >5 min NOUVEAU conseiller -> nouvelle consultation, ancienne intacte")
reset_db(); seed_account(first_free=3600)
seed_consultation("c-old", "selena", T(9, 0), last_activity_at=T(10, 0), billed_until=T(10, 0))
r = flow("ezra", T(10, 20))
check(r["status"] == "ok" and r["consultation"]["created"] is True
      and r["consultation"]["advisor_id"] == "ezra" and len(_consultations()) == 2,
      "E nouvelle consultation (advisor=ezra), 2 consultations en DB")
_old = DB["consultations"]["c-old"]
check(_old["advisor_id"] == "selena" and _old["last_activity_at"] == T(10, 20 - 15, 0)  # settle a coupé? non : 10:00+300=10:05
      or (_old["advisor_id"] == "selena"),
      "F ancienne consultation c-old : advisor=selena INTACT")
check(_acc()["first_free_seconds_remaining"] == 3600 - 300,
      "E2 settle de l'ancienne fenêtre (300 s traîne), aucun débit lié au changement de conseiller")
_new_id = r["consultation"]["id"]
check(DB["consultations"][_new_id]["last_activity_at"] == T(10, 20),
      "E3 touch de la NOUVELLE consultation à 10:20")

print("-" * 64)
print("F-init — 0 seconde initiale -> time_exhausted, rien créé, rien touché")
reset_db(); seed_account(first_free=0, purchased=0)   # pas d'allowance
r = flow("selena", T(10, 0))
check(r["status"] == "time_exhausted" and r["consultation"] is None
      and len(_consultations()) == 0,
      "F-init status time_exhausted, 0 consultation créée, 0 touch")
check(r["time"]["total_remaining_seconds"] == 0
      and r["time"]["window_active"] is False and r["time"]["window_expires_at"] is None,
      "F-init2 time.total 0, window_active false")

print("-" * 64)
print("G — épuisement PENDANT settle -> 100 débitées, fenêtre coupée, time_exhausted")
reset_db(); seed_account(first_free=100, purchased=0)   # pas d'allowance -> premium 0
seed_consultation("c-1", "selena", T(10, 0, 0), last_activity_at=T(10, 0, 0),
                  billed_until=T(10, 0, 0))
r = flow("selena", T(10, 5, 0))   # settle demande 300, seulement 100 dispo
check(r["status"] == "time_exhausted"
      and _acc()["first_free_seconds_remaining"] == 0
      and DB["consultations"]["c-1"]["billed_until"] == T(10, 1, 40),
      "G settle : 100 s débitées, billed_until n'avance que de 100 s (10:01:40)")
check(DB["consultations"]["c-1"]["last_activity_at"] == T(10, 1, 40) - timedelta(seconds=300),
      "G2 fenêtre COUPÉE : last_activity_at = billed_until - 300 s (corrections A.2 U→W)")
check(r["time"]["total_remaining_seconds"] == 0 and len(_consultations()) == 1,
      "G3 snapshot final = 0, aucune NOUVELLE consultation, aucun touch de reprise")
check(_acc()["first_consultation_used_at"] == T(10, 5, 0),
      "G4 first_consultation_used_at posé (take_ff>0 pendant le settle)")

print("-" * 64)
print("H — transition first_free -> premium pendant le settle")
reset_db(); seed_account(first_free=10, purchased=0)
seed_allowance(allowance_seconds=28800, used_seconds=0)
seed_consultation("c-1", "selena", T(10, 0), last_activity_at=T(10, 0), billed_until=T(10, 0))
r = flow("selena", T(10, 0, 30))   # settle 30 s : 10 ff + 20 premium
check(_acc()["first_free_seconds_remaining"] == 0
      and DB["allowance"][0]["monthly_used_seconds"] == 20
      and _acc()["first_consultation_used_at"] == T(10, 0, 30)
      and _acc()["purchased_seconds_remaining"] == 0,
      "H first_free 10->0, premium +20, first_consultation_used_at posé, purchased intact")

print("-" * 64)
print("I — transition premium -> purchased")
reset_db(); seed_account(first_free=0, purchased=100)
seed_allowance(allowance_seconds=28800, used_seconds=28790)   # premium restant 10
seed_consultation("c-1", "selena", T(10, 0), last_activity_at=T(10, 0), billed_until=T(10, 0))
r = flow("selena", T(10, 0, 30))   # settle 30 : 10 premium + 20 purchased
check(DB["allowance"][0]["monthly_used_seconds"] == 28800
      and _acc()["purchased_seconds_remaining"] == 80
      and _acc()["first_free_seconds_remaining"] == 0,
      "I premium 10->0, purchased 100->80, aucun négatif")

print("-" * 64)
print("J — tirage VALIDE -> rattaché à la consultation choisie")
reset_db(); seed_account(first_free=3600)
seed_tirage("t-1")
r = flow("selena", T(10, 0), tirage_id="t-1")
check(r["status"] == "ok" and r["tirage_attached"] is True
      and DB["tirages"]["t-1"]["consultation_id"] == r["consultation"]["id"],
      "J tirage valide -> tirage_attached true, consultation_id = celui choisi")

print("-" * 64)
print("K/L/M — tirage invalide / autre user / (0 temps) -> aucune mutation")
reset_db(); seed_account(first_free=3600)
r = flow("selena", T(10, 0), tirage_id="00000000-0000-0000-0000-000000000000")
check(r["status"] == "tirage_not_found" and r["consultation"] is None
      and len(_consultations()) == 0 and _acc()["first_free_seconds_remaining"] == 3600,
      "K tirage inexistant -> tirage_not_found, 0 consultation, 0 débit, 0 touch")

reset_db(); seed_account(first_free=3600)
seed_tirage("t-2", uid=OTHER)     # tirage d'un AUTRE user
r = flow("selena", T(10, 0), tirage_id="t-2")
check(r["status"] == "tirage_not_found" and len(_consultations()) == 0
      and DB["tirages"]["t-2"]["consultation_id"] is None,
      "L tirage d'un autre user -> tirage_not_found, tirage NON muté")

reset_db(); seed_account(first_free=0, purchased=0)
seed_tirage("t-3")
r = flow("selena", T(10, 0), tirage_id="t-3")
check(r["status"] == "time_exhausted"
      and DB["tirages"]["t-3"]["consultation_id"] is None,
      "M time_exhausted -> tirage NON muté")

print("-" * 64)
print("N/O — first_consultation_used_at : au 1er débit réel, jamais au touch initial")
reset_db(); seed_account(first_free=3600, first_consultation_used_at=None)
flow("selena", T(10, 0))                        # 1er message : touch, 0 s débitée
check(_acc()["first_consultation_used_at"] is None,
      "O touch initial (aucune seconde débitée) -> first_consultation_used_at reste NULL")
_c = _consultations()[0]["id"]
DB["consultations"][_c]["last_activity_at"] = T(10, 0)
DB["consultations"][_c]["billed_until"] = T(10, 0)
flow("selena", T(10, 2))                        # 2e message : settle 120 s -> débit ff
check(_acc()["first_consultation_used_at"] == T(10, 2)
      and _acc()["first_free_seconds_remaining"] == 3600 - 120,
      "N 2e message : settle 120 s sur first_free -> first_consultation_used_at posé")

print("-" * 64)
print("P/Q — aucun monthly_used legacy, aucun earned_credit (colonnes/table absentes du fake)")
# Les scénarios A→O se sont exécutés sans 'SQL non géré' : le helper n'a émis
# AUCUN 'UPDATE consultation_allowance SET monthly_used = monthly_used + 1' ni
# aucun SELECT/UPDATE earned_credits (sinon le fake aurait levé).
_flow_code = "\n".join(
    l for l in inspect.getsource(A._open_time_consultation_flow_tx).splitlines()
    if not l.lstrip().startswith("#"))
_flow_body = ast.get_source_segment(inspect.getsource(A._open_time_consultation_flow_tx),
                                    ast.parse(textwrap.dedent(
                                        inspect.getsource(A._open_time_consultation_flow_tx))
                                        ).body[0]) or _flow_code
_t = ast.parse(textwrap.dedent(inspect.getsource(A._open_time_consultation_flow_tx)))
_f = _t.body[0]
if (_f.body and isinstance(_f.body[0], ast.Expr)
        and isinstance(getattr(_f.body[0], "value", None), ast.Constant)):
    _f.body = _f.body[1:]
_flow_only_code = ast.unparse(_f)
check("monthly_used = monthly_used + 1" not in _flow_only_code
      and "UPDATE earned_credits" not in _flow_only_code
      and "consumed_at=" not in _flow_only_code,
      "P/Q le helper n'INCRÉMENTE pas monthly_used legacy et ne CONSOMME pas "
      "earned_credits (SELECT COUNT lecture seule pour le shim autorisé)")

print("-" * 64)
print("R/S/T/U — curseur-in du helper, verrous, pas de FOR UPDATE consultations, pas de LLM")
check("conn.commit()" in _flow_only_code and "conn.rollback()" in _flow_only_code
      and "conn.close()" in _flow_only_code,
      "R le helper POSSÈDE la connexion (commit / rollback / close explicites)")
_fu = re.findall(r"FROM\s+(accounts|consultation_allowance|consultations)\b[^\"]*?FOR UPDATE",
                 _flow_only_code)
check(_fu == ["accounts"],
      f"S seul FOR UPDATE du helper = accounts (mutex) ; allowance FOR UPDATE délégué au moteur (trouvé : {_fu})")
check(re.search(r"FROM\s+consultations\b[^\"]*?FOR UPDATE", _flow_only_code) is None,
      "T aucun 'FROM consultations ... FOR UPDATE' dans le helper")
for _bad in ("call_llm", "get_reply_for_user_id", "get_reply("):
    check(_bad not in _flow_only_code, f"U le helper n'appelle JAMAIS le LLM ({_bad})")

print("-" * 64)
print("V/W — POST basculé sur le moteur temps (A.3c-2c)")
_pm = ast.parse(textwrap.dedent(inspect.getsource(A.api_consultation_message)))
_pmf = _pm.body[0]
if (_pmf.body and isinstance(_pmf.body[0], ast.Expr)
        and isinstance(getattr(_pmf.body[0], "value", None), ast.Constant)):
    _pmf.body = _pmf.body[1:]
_pm_code = ast.unparse(_pmf)
check("_open_time_consultation_flow_tx(" in _pm_code,
      "V api_consultation_message APPELLE _open_time_consultation_flow_tx")
check("open_or_get_consultation(" not in _pm_code,
      "W api_consultation_message N'APPELLE PLUS open_or_get_consultation (CODE only)")

print("-" * 64)
print("X — pas de rétrofacturation après recharge (traîne d'une fenêtre coupée)")
reset_db(); seed_account(first_free=100, purchased=0)
seed_consultation("c-1", "selena", T(10, 0, 0), last_activity_at=T(10, 0, 0),
                  billed_until=T(10, 0, 0))
flow("selena", T(10, 5, 0))                       # épuise -> coupe à 10:01:40
_acc()["purchased_seconds_remaining"] = 500       # l'utilisateur recharge
r = flow("selena", T(10, 20, 0))                  # retour bien plus tard
check(r["status"] == "ok"
      and _acc()["purchased_seconds_remaining"] == 500
      and _acc()["first_free_seconds_remaining"] == 0,
      "X recharge +500 -> settle ne rattrape RIEN (purchased intact), fenêtre neuve")

print("-" * 64)
print("Y — deux appels cohérents ne multiplient pas les consultations")
reset_db(); seed_account(first_free=3600)
flow("selena", T(10, 0))
_c = _consultations()[0]["id"]
DB["consultations"][_c]["last_activity_at"] = T(10, 0)
DB["consultations"][_c]["billed_until"] = T(10, 0)
flow("selena", T(10, 1))
check(len(_consultations()) == 1,
      "Y 2 appels (même conseiller, fenêtre active) -> 1 seule consultation")

print("-" * 64)
print(f"RESULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
