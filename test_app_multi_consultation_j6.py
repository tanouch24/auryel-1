"""
test_app_multi_consultation_j6.py — J6-B2 : backend multi-consultations.

Modèle V1 : UNE discussion persistante par conseiller (max 10). Ce fichier
couvre les capacités nouvelles / étendues :

    GET  /api/consultation/list                        (A)
    POST /api/consultation/open                        (B)
    GET  /api/consultation/messages?consultation_id=   (C)
    POST /api/consultation/message {consultation_id}   (D)
    facturation multi-fil (settle de toutes les fenêtres)  (E)
    GET  /api/consultation/state sans cutoff `expires_at`  (F)

100 % local : psycopg2 mocké, get_conn -> fausse DB mémoire (accounts /
consultation_allowance / consultations / messages / app_sessions / app_profiles
/ earned_credits / tirages). call_llm jamais atteint (get_reply_for_user_id
monkeypatché pour les tests d'endpoint ; le moteur temps tourne pour de vrai).
Style aligné sur test_time_consultation_flow.py + test_app_consultation.py.
"""

import copy
import sys
from datetime import datetime, timedelta, timezone
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
        print(f"OK  {label}")
    else:
        _STATE["fail"] += 1
        print(f"XX  {label}")


UID = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
PS = datetime(2026, 1, 1, tzinfo=timezone.utc)
PE = datetime(2027, 1, 1, tzinfo=timezone.utc)


def T(h, m, s=0):
    return datetime(2026, 9, 1, h, m, s, tzinfo=timezone.utc)


DB = {"accounts": {}, "allowance": [], "consultations": {}, "messages": [],
      "sessions": {}, "profiles": {}, "earned": [], "tirages": {}, "seq": [0]}


def reset_db():
    for k in ("accounts", "consultations", "sessions", "profiles"):
        DB[k].clear()
    DB["allowance"].clear()
    DB["messages"].clear()
    DB["earned"].clear()
    DB["tirages"].clear()
    DB["seq"][0] = 0


def seed_account(uid=UID, deleted_at=None, first_free=3600, purchased=0,
                 first_consultation_used_at=None):
    DB["accounts"][str(uid)] = {
        "user_id": str(uid), "email": f"{uid}@ex.com", "deleted_at": deleted_at,
        "first_free_seconds_remaining": first_free,
        "purchased_seconds_remaining": purchased,
        "first_consultation_used_at": first_consultation_used_at,
    }


def seed_allowance(uid=UID, allowance_seconds=28800, used_seconds=0):
    DB["allowance"].append({
        "user_id": str(uid), "period_start": PS, "period_end": PE,
        "monthly_allowance_seconds": allowance_seconds,
        "monthly_used_seconds": used_seconds,
    })


def seed_profile(uid=UID, guide="selena"):
    row = {f: "" for f in A._APP_PROFILE_FIELDS}
    row["user_id"] = str(uid)
    row["guide"] = guide
    # Contrôle 18+ serveur (v41) : consultation/open exigent une date de
    # naissance adulte au profil. Ces tests ne portent pas sur le gate.
    row["date_naissance"] = "2000-01-01"
    for c in ("nb_echanges", "nb_echanges_decouverte", "nb_echanges_dernier_tirage",
              "nb_echanges_dernier_psaume", "niveau_detresse", "niveau_attachement",
              "chemin_de_vie"):
        row[c] = 0
    row["tirage_propose_en_attente"] = False
    row["onboarding_done"] = True
    DB["profiles"][str(uid)] = row


def seed_consultation(cid, advisor_id, started_at, last_activity_at=None,
                      billed_until=None, uid=UID):
    DB["consultations"][str(cid)] = {
        "id": str(cid), "user_id": str(uid), "advisor_id": advisor_id,
        "started_at": started_at, "expires_at": started_at + timedelta(hours=2),
        "credit_source": "time", "created_at": started_at,
        "last_activity_at": last_activity_at, "billed_until": billed_until,
    }


def seed_message(uid, cid, role, content, ts=None):
    DB["seq"][0] += 1
    DB["messages"].append({"id": DB["seq"][0], "user_id": str(uid),
                           "consultation_id": str(cid) if cid else None,
                           "role": role, "content": content,
                           "timestamp": ts or A._utcnow().isoformat()})


def seed_session(uid=UID, token="tok-" + UID):
    th = A._hash_session_token(token)
    DB["sessions"][th] = {"id": "s-" + str(uid), "user_id": str(uid),
                          "token_hash": th, "revoked_at": None,
                          "expires_at": datetime.utcnow() + timedelta(days=30)}
    return token


def _norm(sql):
    return " ".join(sql.split())


class FakeCursor:
    def __init__(self):
        self._result = None
        self._rows = None
        self.rowcount = -1

    def fetchone(self):
        return self._result

    def fetchall(self):
        return self._rows or []

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = _norm(sql)
        p = tuple(params or ())
        self._result = None
        self._rows = None
        self.rowcount = -1

        # ---- sessions / auth ----
        if k == ("SELECT s.id, s.user_id, s.expires_at, s.revoked_at, a.email, "
                 "a.deleted_at FROM app_sessions s JOIN accounts a ON "
                 "a.user_id = s.user_id WHERE s.token_hash=%s"):
            (th,) = p
            s = DB["sessions"].get(th)
            if not s:
                self._result = None
            else:
                a = DB["accounts"].get(s["user_id"])
                self._result = ((s["id"], s["user_id"], s["expires_at"],
                                 s["revoked_at"], a["email"], a["deleted_at"])
                                if a else None)
        elif k == "UPDATE app_sessions SET last_used_at=%s WHERE id=%s":
            self.rowcount = 1

        # ---- app_profiles ----
        elif k == _norm("SELECT " + ", ".join(A._APP_PROFILE_FIELDS)
                        + " FROM app_profiles WHERE user_id=%s"):
            (uid,) = p
            r = DB["profiles"].get(str(uid))
            self._result = tuple(r[f] for f in A._APP_PROFILE_FIELDS) if r else None
        elif k.startswith("INSERT INTO app_profiles (user_id, guide, onboarding_done"):
            uid = p[0]
            if str(uid) not in DB["profiles"]:
                seed_profile(uid)
        elif k.startswith("UPDATE app_profiles SET ") and k.endswith(" WHERE user_id=%s"):
            self.rowcount = 1

        # ---- accounts ----
        elif k == "SELECT deleted_at FROM accounts WHERE user_id=%s":
            (uid,) = p
            a = DB["accounts"].get(str(uid))
            self._result = (a["deleted_at"],) if a else None
        elif k == "SELECT email FROM accounts WHERE user_id=%s":
            (uid,) = p
            a = DB["accounts"].get(str(uid))
            self._result = (a["email"],) if a else None
        elif k == ("SELECT user_id FROM accounts "
                   "WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE"):
            (uid,) = p
            a = DB["accounts"].get(str(uid))
            self._result = (a["user_id"],) if a and a["deleted_at"] is None else None
        elif k == ("SELECT user_id, first_consultation_used_at FROM accounts "
                   "WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE"):
            (uid,) = p
            a = DB["accounts"].get(str(uid))
            self._result = ((a["user_id"], a["first_consultation_used_at"])
                            if a and a["deleted_at"] is None else None)
        elif k in ("SELECT first_free_seconds_remaining, purchased_seconds_remaining "
                   "FROM accounts WHERE user_id=%s",
                   "SELECT first_free_seconds_remaining, purchased_seconds_remaining "
                   "FROM accounts WHERE user_id=%s FOR UPDATE"):
            (uid,) = p
            a = DB["accounts"].get(str(uid))
            self._result = ((a["first_free_seconds_remaining"],
                             a["purchased_seconds_remaining"]) if a else None)
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

        # ---- consultation_allowance ----
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
        elif k == ("UPDATE consultation_allowance SET monthly_used_seconds=%s "
                   "WHERE user_id=%s AND period_start=%s"):
            used, uid, ps = p
            a = next((x for x in DB["allowance"]
                      if x["user_id"] == str(uid) and x["period_start"] == ps), None)
            if a:
                a["monthly_used_seconds"] = used
                self.rowcount = 1
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

        # ---- earned_credits ----
        elif k == ("SELECT COUNT(*) FROM earned_credits "
                   "WHERE user_id=%s AND consumed_at IS NULL"):
            (uid,) = p
            n = sum(1 for e in DB["earned"]
                    if e["user_id"] == str(uid) and e["consumed_at"] is None)
            self._result = (n,)

        # ---- consultations ----
        elif k == "SELECT id FROM tirages WHERE id=%s AND user_id=%s":
            tid, uid = p
            t = DB["tirages"].get(str(tid))
            self._result = (t["id"],) if t and t["user_id"] == str(uid) else None
        elif k == ("SELECT DISTINCT ON (advisor_id) id, advisor_id, started_at, "
                   "last_activity_at FROM consultations WHERE user_id=%s ORDER BY "
                   "advisor_id, COALESCE(last_activity_at, started_at) DESC, "
                   "started_at DESC"):
            (uid,) = p
            by_adv = {}
            for c in DB["consultations"].values():
                if c["user_id"] != str(uid):
                    continue
                key = c["last_activity_at"] or c["started_at"]
                cur = by_adv.get(c["advisor_id"])
                if cur is None or key > (cur["last_activity_at"] or cur["started_at"]):
                    by_adv[c["advisor_id"]] = c
            self._rows = [(c["id"], c["advisor_id"], c["started_at"],
                           c["last_activity_at"]) for c in by_adv.values()]
        elif k == ("SELECT content FROM messages WHERE user_id=%s AND "
                   "consultation_id=%s AND role IN ('user','assistant') "
                   "ORDER BY id DESC LIMIT 1"):
            uid, cid = p
            rows = sorted([m for m in DB["messages"]
                           if m["user_id"] == str(uid)
                           and str(m["consultation_id"]) == str(cid)
                           and m["role"] in ("user", "assistant")],
                          key=lambda m: m["id"], reverse=True)
            self._result = (rows[0]["content"],) if rows else None
        elif k == ("SELECT id, advisor_id, started_at, expires_at, credit_source "
                   "FROM consultations WHERE user_id=%s AND advisor_id=%s "
                   "ORDER BY started_at DESC LIMIT 1"):
            uid, adv = p
            rows = sorted([c for c in DB["consultations"].values()
                           if c["user_id"] == str(uid) and c["advisor_id"] == adv],
                          key=lambda c: c["started_at"], reverse=True)
            if rows:
                c0 = rows[0]
                self._result = (c0["id"], c0["advisor_id"], c0["started_at"],
                                c0["expires_at"], c0["credit_source"])
        elif k == ("SELECT advisor_id FROM consultations WHERE id=%s AND user_id=%s"):
            cid, uid = p
            c0 = DB["consultations"].get(str(cid))
            self._result = ((c0["advisor_id"],)
                            if c0 and c0["user_id"] == str(uid) else None)
        elif k == ("SELECT id, advisor_id FROM consultations "
                   "WHERE id=%s AND user_id=%s"):
            cid, uid = p
            c0 = DB["consultations"].get(str(cid))
            self._result = ((c0["id"], c0["advisor_id"])
                            if c0 and c0["user_id"] == str(uid) else None)
        elif k == "SELECT user_id FROM consultations WHERE id=%s":
            (cid,) = p
            c0 = DB["consultations"].get(str(cid))
            self._result = (c0["user_id"],) if c0 else None
        elif k == ("SELECT started_at, expires_at, credit_source "
                   "FROM consultations WHERE id=%s"):
            (cid,) = p
            c0 = DB["consultations"].get(str(cid))
            self._result = ((c0["started_at"], c0["expires_at"],
                             c0["credit_source"]) if c0 else None)
        elif k == ("SELECT id FROM consultations WHERE user_id=%s "
                   "ORDER BY started_at DESC LIMIT 1"):
            (uid,) = p
            rows = sorted([c for c in DB["consultations"].values()
                           if c["user_id"] == str(uid)],
                          key=lambda c: c["started_at"], reverse=True)
            self._result = (rows[0]["id"],) if rows else None
        elif k == ("SELECT id FROM consultations "
                   "WHERE user_id=%s AND last_activity_at IS NOT NULL"):
            (uid,) = p
            self._rows = [(c["id"],) for c in DB["consultations"].values()
                          if c["user_id"] == str(uid)
                          and c["last_activity_at"] is not None]
        elif k == ("SELECT id, advisor_id, last_activity_at, billed_until "
                   "FROM consultations WHERE user_id=%s "
                   "ORDER BY started_at DESC LIMIT 1"):
            (uid,) = p
            rows = sorted([c for c in DB["consultations"].values()
                           if c["user_id"] == str(uid)],
                          key=lambda c: c["started_at"], reverse=True)
            if rows:
                c0 = rows[0]
                self._result = (c0["id"], c0["advisor_id"],
                                c0["last_activity_at"], c0["billed_until"])
        elif k == ("SELECT id, advisor_id, started_at, expires_at, credit_source, "
                   "last_activity_at, billed_until FROM consultations "
                   "WHERE user_id=%s ORDER BY started_at DESC LIMIT 1"):
            (uid,) = p
            rows = sorted([c for c in DB["consultations"].values()
                           if c["user_id"] == str(uid)],
                          key=lambda c: c["started_at"], reverse=True)
            if rows:
                c0 = rows[0]
                self._result = (c0["id"], c0["advisor_id"], c0["started_at"],
                                c0["expires_at"], c0["credit_source"],
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
            if t and t["user_id"] == str(uid) and t.get("consultation_id") is None:
                t["consultation_id"] = str(cid)
                self.rowcount = 1
            else:
                self.rowcount = 0

        # ---- messages ----
        elif k == ("INSERT INTO messages (user_id, phone, role, content, timestamp, "
                   "consultation_id) VALUES (%s, NULL, %s, %s, %s, %s)"):
            uid, role, content, ts, cid = p
            seed_message(uid, cid, role, content, ts)
        elif k == ("SELECT id, role, content, timestamp FROM messages WHERE user_id=%s "
                   "AND consultation_id=%s AND role IN ('user','assistant') "
                   "ORDER BY timestamp ASC, id ASC"):
            uid, cid = p
            rows = sorted([m for m in DB["messages"]
                           if m["user_id"] == str(uid)
                           and str(m["consultation_id"]) == str(cid)
                           and m["role"] in ("user", "assistant")],
                          key=lambda m: (m["timestamp"], m["id"]))
            self._rows = [(m["id"], m["role"], m["content"], m["timestamp"]) for m in rows]
        elif k == ("SELECT id FROM messages WHERE user_id=%s "
                   "AND consultation_id=%s AND role='assistant' "
                   "ORDER BY id DESC LIMIT 1"):
            uid, cid = p
            rows = sorted([m for m in DB["messages"]
                           if m["user_id"] == str(uid)
                           and str(m["consultation_id"]) == str(cid)
                           and m["role"] == "assistant"],
                          key=lambda m: m["id"], reverse=True)
            self._result = (rows[0]["id"],) if rows else None
        elif k in ("SELECT role,content FROM messages WHERE user_id=%s "
                   "AND consultation_id=%s ORDER BY id DESC LIMIT %s",):
            uid, cid, lim = p
            rows = sorted([m for m in DB["messages"]
                           if m["user_id"] == str(uid)
                           and str(m["consultation_id"]) == str(cid)],
                          key=lambda m: m["id"], reverse=True)[:lim]
            self._rows = [(m["role"], m["content"]) for m in rows]
        elif k == ("SELECT role,content FROM messages WHERE user_id=%s "
                   "ORDER BY id DESC LIMIT %s"):
            uid, lim = p
            rows = sorted([m for m in DB["messages"] if m["user_id"] == str(uid)],
                          key=lambda m: m["id"], reverse=True)[:lim]
            self._rows = [(m["role"], m["content"]) for m in rows]

        else:
            raise AssertionError("SQL non géré par le fake J6-B2 : " + k)


class FakeConn:
    def __init__(self):
        self._snap = copy.deepcopy(DB)

    def cursor(self):
        return FakeCursor()

    def commit(self):
        self._snap = copy.deepcopy(DB)

    def rollback(self):
        for key in DB:
            if isinstance(DB[key], dict):
                DB[key].clear()
                DB[key].update(self._snap[key])
            else:
                DB[key][:] = self._snap[key]

    def close(self):
        pass


A.get_conn = lambda: FakeConn()
A.app.config["TESTING"] = True
try:
    A.limiter.enabled = False
except Exception:
    pass
client = A.app.test_client()


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def _fresh(uid=UID, first_free=3600, allowance_used=0):
    reset_db()
    seed_account(uid, first_free=first_free)
    seed_allowance(uid, used_seconds=allowance_used)
    seed_profile(uid, guide="selena")
    return seed_session(uid, token="tok-" + str(uid))


CID_SEL = "aaaaaaaa-0000-4000-8000-000000000001"
CID_EZR = "bbbbbbbb-0000-4000-8000-000000000002"


print("=" * 64)
print("J6-B2 — A. GET /api/consultation/list")
print("=" * 64)

# 1. sans auth -> 401
reset_db()
check(client.get("/api/consultation/list").status_code == 401,
      "1 GET /list sans Bearer -> 401")

# 2. user sans consultation -> []
tok = _fresh()
r = client.get("/api/consultation/list", headers=_hdr(tok))
check(r.status_code == 200 and r.get_json() == {"consultations": []},
      "2 user sans discussion -> { consultations: [] }")

# 3/4/5/6. Séléna + Ezra -> 2 entrées, tri récence, preview cloisonnée
tok = _fresh()
seed_consultation(CID_SEL, "selena", T(10, 0), last_activity_at=T(10, 5))
seed_consultation(CID_EZR, "ezra", T(11, 0), last_activity_at=T(11, 30))
seed_message(UID, CID_SEL, "user", "je parle à Séléna de mon travail")
seed_message(UID, CID_EZR, "assistant", "Ezra te répond sur ta famille")
j = client.get("/api/consultation/list", headers=_hdr(tok)).get_json()
cons = j["consultations"]
check(len(cons) == 2, "3 Séléna + Ezra -> 2 entrées")
check([c["advisor_id"] for c in cons] == ["ezra", "selena"],
      "4 tri : fil le plus récemment actif d'abord (Ezra avant Séléna)")
by_adv = {c["advisor_id"]: c for c in cons}
check(by_adv["ezra"]["preview"] == "Ezra te répond sur ta famille"
      and by_adv["selena"]["preview"] == "je parle à Séléna de mon travail",
      "5 preview = dernier message DE CE fil")
check("Ezra" not in by_adv["selena"]["preview"]
      and "Séléna" not in by_adv["ezra"]["preview"],
      "6 preview ne fuit jamais le contenu d'un autre conseiller")

# 7. plusieurs anciennes lignes Ezra -> une seule entrée, la plus récente
tok = _fresh()
seed_consultation("cccccccc-0000-4000-8000-000000000003", "ezra", T(8, 0))
seed_consultation("dddddddd-0000-4000-8000-000000000004", "ezra", T(12, 0),
                  last_activity_at=T(12, 10))
j = client.get("/api/consultation/list", headers=_hdr(tok)).get_json()
check(len(j["consultations"]) == 1
      and j["consultations"][0]["id"] == "dddddddd-0000-4000-8000-000000000004",
      "7 doublons Ezra -> 1 seule entrée, la plus récemment active")

# 8. autre user jamais exposé
tok = _fresh()
seed_consultation(CID_SEL, "selena", T(10, 0), uid=UID)
seed_consultation("eeeeeeee-0000-4000-8000-000000000005", "luna", T(10, 0),
                  uid=OTHER)
j = client.get("/api/consultation/list", headers=_hdr(tok)).get_json()
check([c["advisor_id"] for c in j["consultations"]] == ["selena"],
      "8 aucune consultation d'un autre user exposée")

# 9. expires_at ancien n'exclut pas le fil
tok = _fresh()
old = T(1, 0)
seed_consultation(CID_SEL, "selena", old, last_activity_at=old)
DB["consultations"][CID_SEL]["expires_at"] = old + timedelta(hours=2)  # < now
j = client.get("/api/consultation/list", headers=_hdr(tok)).get_json()
check(len(j["consultations"]) == 1,
      "9 fil dont expires_at est passé reste listé")

# window_active : une seule à la fois
tok = _fresh()
now = A._utcnow()
seed_consultation(CID_SEL, "selena", now - timedelta(seconds=60),
                  last_activity_at=now - timedelta(seconds=60))
seed_consultation(CID_EZR, "ezra", now - timedelta(seconds=30),
                  last_activity_at=now - timedelta(seconds=30))
j = client.get("/api/consultation/list", headers=_hdr(tok)).get_json()
actives = [c for c in j["consultations"] if c["window_active"]]
check(len(actives) == 1 and actives[0]["advisor_id"] == "ezra",
      "9b une seule window_active exposée (le fil le plus récemment actif)")


print("=" * 64)
print("J6-B2 — B. POST /api/consultation/open")
print("=" * 64)

# 10. sans auth -> 401
check(client.post("/api/consultation/open", json={"advisor_id": "ezra"}).status_code == 401,
      "10 POST /open sans Bearer -> 401")

# 11. advisor invalide -> 400
tok = _fresh()
check(client.post("/api/consultation/open", json={"advisor_id": "bidon"},
                  headers=_hdr(tok)).status_code == 400,
      "11 advisor_id inconnu -> 400")

# 12. open Ezra première fois -> crée
tok = _fresh()
r = client.post("/api/consultation/open", json={"advisor_id": "ezra"},
                headers=_hdr(tok))
j = r.get_json()
check(r.status_code == 200 and j["consultation"]["advisor_id"] == "ezra"
      and j["consultation"]["opened_now"] is True,
      "12 open Ezra (1re fois) -> crée le fil")
ezra_id = j["consultation"]["id"]

# 13. open Ezra 2e fois -> même fil (pas de doublon)
r2 = client.post("/api/consultation/open", json={"advisor_id": "ezra"},
                 headers=_hdr(tok))
j2 = r2.get_json()
check(j2["consultation"]["id"] == ezra_id and j2["consultation"]["opened_now"] is False,
      "13 open Ezra (2e fois) -> reprend le MÊME fil, aucun doublon")
check(sum(1 for c in DB["consultations"].values()
          if c["advisor_id"] == "ezra") == 1,
      "13b une seule ligne Ezra en base")

# 14. open Séléna -> autre id
r3 = client.post("/api/consultation/open", json={"advisor_id": "selena"},
                 headers=_hdr(tok))
sel_id = r3.get_json()["consultation"]["id"]
check(sel_id != ezra_id, "14 open Séléna -> fil distinct")

# 15/16. aucun temps débité, aucune fenêtre facturable
check(DB["accounts"][UID]["first_free_seconds_remaining"] == 3600
      and DB["accounts"][UID]["first_consultation_used_at"] is None,
      "15 open ne débite AUCUN temps")
check(all(c["last_activity_at"] is None and c["billed_until"] is None
          for c in DB["consultations"].values()),
      "16 open n'ouvre AUCUNE fenêtre facturable (last_activity_at/billed_until NULL)")

# 17. app_profiles.guide inchangé
check(DB["profiles"][UID]["guide"] == "selena",
      "17 open Ezra ne modifie PAS app_profiles.guide")

# 18. autre user isolé
tok_o = seed_session(OTHER, token="tok-" + OTHER)
seed_account(OTHER)
seed_allowance(OTHER)
seed_profile(OTHER)
r = client.post("/api/consultation/open", json={"advisor_id": "ezra"},
                headers=_hdr(tok_o))
check(r.get_json()["consultation"]["id"] != ezra_id,
      "18 open d'un autre user -> ne réutilise jamais le fil d'un tiers")


print("=" * 64)
print("J6-B2 — C. GET /api/consultation/messages?consultation_id=")
print("=" * 64)

tok = _fresh()
seed_consultation(CID_SEL, "selena", T(10, 0))
seed_consultation(CID_EZR, "ezra", T(11, 0))
seed_message(UID, CID_SEL, "user", "SECRET SELENA")
seed_message(UID, CID_SEL, "assistant", "Séléna entendu")
seed_message(UID, CID_EZR, "user", "SECRET EZRA")

# 19/20. ciblé -> uniquement ce fil
j = client.get(f"/api/consultation/messages?consultation_id={CID_EZR}",
               headers=_hdr(tok)).get_json()
blob = " ".join(m["content"] for m in j["messages"])
check(j["consultation_id"] == CID_EZR and "SECRET EZRA" in blob
      and "SECRET SELENA" not in blob,
      "19 messages ciblés Ezra -> uniquement Ezra")
j = client.get(f"/api/consultation/messages?consultation_id={CID_SEL}",
               headers=_hdr(tok)).get_json()
blob = " ".join(m["content"] for m in j["messages"])
check("SECRET SELENA" in blob and "SECRET EZRA" not in blob,
      "20 messages ciblés Séléna -> uniquement Séléna")

# 21. consultation d'un autre user -> 404
seed_consultation("ffffffff-0000-4000-8000-000000000006", "luna", T(9, 0),
                  uid=OTHER)
r = client.get("/api/consultation/messages?consultation_id="
               "ffffffff-0000-4000-8000-000000000006", headers=_hdr(tok))
check(r.status_code == 404, "21 messages d'un fil d'un autre user -> 404")

# 22. id inconnu -> 404, jamais de repli legacy
r = client.get("/api/consultation/messages?consultation_id="
               "12345678-0000-4000-8000-000000000000", headers=_hdr(tok))
check(r.status_code == 404, "22 consultation_id inconnu -> 404 (aucun repli sur le dernier fil)")
r = client.get("/api/consultation/messages?consultation_id=pas-un-uuid",
               headers=_hdr(tok))
check(r.status_code == 404, "22b consultation_id malformé -> 404")

# 23. sans paramètre -> compat legacy (dernier fil)
j = client.get("/api/consultation/messages", headers=_hdr(tok)).get_json()
check(j["consultation_id"] == CID_EZR,
      "23 sans consultation_id -> comportement legacy (fil le plus récent)")


print("=" * 64)
print("J6-B2 — D. POST /api/consultation/message {consultation_id}")
print("=" * 64)

_REPLY = "Réponse du conseiller ciblé."


def _post_targeted(tok, cid, message="parle-moi", tirage_id=None):
    body = {"message": message, "consultation_id": cid}
    if tirage_id is not None:
        body["tirage_id"] = tirage_id
    rep = MagicMock(return_value=_REPLY)
    with patch.object(A, "get_reply_for_user_id", rep), \
         patch.object(A, "_app_persist_emotional_context", MagicMock()), \
         patch.object(A, "maybe_refresh_advisor_memory", MagicMock()):
        r = client.post("/api/consultation/message", json=body, headers=_hdr(tok))
    return r, rep


# 24/25/26. POST ciblé reste sur le bon conseiller (jamais app_profiles.guide)
tok = _fresh()
seed_profile(UID, guide="raphael")   # conseiller profil VOLONTAIREMENT différent
seed_consultation(CID_EZR, "ezra", T(11, 0))
seed_consultation(CID_SEL, "selena", T(10, 0))
r, rep = _post_targeted(tok, CID_EZR)
j = r.get_json()
check(r.status_code == 200 and j["consultation"]["id"] == CID_EZR
      and j["consultation"]["advisor_id"] == "ezra",
      "24 POST ciblé Ezra -> réponse rattachée au fil Ezra")
check(rep.call_args.kwargs.get("advisor_override") == "ezra"
      and rep.call_args.kwargs.get("consultation_id") == CID_EZR,
      "25 get_reply_for_user_id reçoit advisor_override='ezra' + consultation_id=Ezra")
r, rep = _post_targeted(tok, CID_SEL)
check(r.get_json()["consultation"]["advisor_id"] == "selena"
      and rep.call_args.kwargs.get("advisor_override") == "selena",
      "26 POST ciblé Séléna -> conseiller = 'selena' ; app_profiles.guide "
      "('raphael') n'écrase JAMAIS le conseiller du fil")
check(DB["profiles"][UID]["guide"] == "raphael",
      "26b POST ciblé ne modifie pas app_profiles.guide")

# 27. user + assistant persistés avec le bon consultation_id
#     (get_reply mimé : persiste comme _reply_core, via l'accessor cloisonné)
tok = _fresh()
seed_consultation(CID_EZR, "ezra", T(11, 0))


def _fake_reply(user_id, msg, advisor_override=None, consultation_id=None, **kw):
    A.add_message_for_user_id(user_id, "user", msg,
                              consultation_id=consultation_id)
    A.add_message_for_user_id(user_id, "assistant", _REPLY,
                              consultation_id=consultation_id)
    return _REPLY


with patch.object(A, "get_reply_for_user_id", side_effect=_fake_reply), \
     patch.object(A, "_app_persist_emotional_context", MagicMock()), \
     patch.object(A, "maybe_refresh_advisor_memory", MagicMock()):
    client.post("/api/consultation/message",
                json={"message": "mon message ciblé", "consultation_id": CID_EZR},
                headers=_hdr(tok))
persisted = [m for m in DB["messages"] if str(m["consultation_id"]) == CID_EZR]
check(any(m["role"] == "user" and m["content"] == "mon message ciblé"
          for m in persisted)
      and any(m["role"] == "assistant" and m["content"] == _REPLY
              for m in persisted),
      "27 messages user + assistant écrits avec consultation_id=Ezra")
check(all(str(m["consultation_id"]) == CID_EZR for m in DB["messages"]),
      "27b aucun message rattaché à un autre fil")

# 28. tirage rattaché au bon fil
tok = _fresh()
seed_consultation(CID_EZR, "ezra", T(11, 0))
TID = "99999999-0000-4000-8000-000000000009"
DB["tirages"][TID] = {"id": TID, "user_id": UID, "card_keys": ["le_fou"],
                      "advisor_id": None, "consultation_id": None,
                      "created_at": T(9, 0)}
# tirage gate: on ajoute juste la branche via patch get_tirage pour le contexte
with patch.object(A, "get_tirage",
                  MagicMock(return_value={"id": TID, "user_id": UID,
                                          "card_keys": '["le_fou"]',
                                          "consultation_id": None})):
    r, rep = _post_targeted(tok, CID_EZR, tirage_id=TID)
check(r.status_code == 200,
      "28 POST ciblé avec tirage_id -> 200 (tirage transmis au flux du fil Ezra)")
check(rep.call_args.kwargs.get("consultation_id") == CID_EZR,
      "28b le contexte tirage part vers le consultation_id du fil ciblé")

# 29. consultation d'un autre user -> 404
tok = _fresh()
seed_consultation("ffffffff-0000-4000-8000-000000000006", "luna", T(9, 0),
                  uid=OTHER)
r, _ = _post_targeted(tok, "ffffffff-0000-4000-8000-000000000006")
check(r.status_code == 404, "29 POST ciblé sur un fil d'un autre user -> 404")
r, _ = _post_targeted(tok, "00000000-0000-4000-8000-000000000000")
check(r.status_code == 404, "29b POST ciblé sur un id inconnu -> 404")

# 30. sans consultation_id -> compat legacy
tok = _fresh()
rep = MagicMock(return_value=_REPLY)
with patch.object(A, "get_reply_for_user_id", rep), \
     patch.object(A, "_app_persist_emotional_context", MagicMock()), \
     patch.object(A, "maybe_refresh_advisor_memory", MagicMock()):
    r = client.post("/api/consultation/message", json={"message": "salut"},
                    headers=_hdr(tok))
check(r.status_code == 200 and rep.call_args.kwargs.get("advisor_override") == "selena",
      "30 sans consultation_id -> legacy (conseiller préféré du profil)")


print("=" * 64)
print("J6-B2 — E. FACTURATION MULTI-FIL (via _open_time_consultation_flow_tx)")
print("=" * 64)


def _flow(target, now, preferred="selena"):
    return A._open_time_consultation_flow_tx(UID, preferred, None, now,
                                             target_consultation_id=target)


# 31/32/33/34. Séléna fenêtre active -> switch Ezra : tranche Séléna settled 1x,
#              pas de double débit, Ezra repart, wallet global.
reset_db()
seed_account(UID, first_free=0, purchased=0)
seed_allowance(UID, allowance_seconds=28800, used_seconds=0)
seed_consultation(CID_SEL, "selena", T(10, 0, 0),
                  last_activity_at=T(10, 0, 0), billed_until=T(10, 0, 0))
seed_consultation(CID_EZR, "ezra", T(10, 1, 0))
# switch vers Ezra 120 s après le dernier message Séléna
res = _flow(CID_EZR, T(10, 2, 0))
used_after_switch = DB["allowance"][0]["monthly_used_seconds"]
check(res["status"] == "ok" and res["consultation"]["id"] == CID_EZR
      and res["consultation"]["advisor_id"] == "ezra",
      "31 switch Séléna->Ezra : le flux cible bien le fil Ezra")
check(used_after_switch == 120,
      "32 tranche réellement due de Séléna (120 s) débitée UNE fois")
check(DB["consultations"][CID_SEL]["billed_until"] == T(10, 2, 0),
      "32b billed_until Séléna avancé de 120 s exactement")
# Ezra a ouvert sa fenêtre maintenant
check(DB["consultations"][CID_EZR]["last_activity_at"] == T(10, 2, 0)
      and DB["consultations"][CID_EZR]["billed_until"] == T(10, 2, 0),
      "33 Ezra : fenêtre ouverte à l'instant du switch (billed_until = now)")
# re-switch Ezra plus tard : Séléna finit sa fenêtre (jusqu'à 10:05), Ezra facturé
res = _flow(CID_EZR, T(10, 10, 0))
used_total = DB["allowance"][0]["monthly_used_seconds"]
# Séléna : reste [10:02 -> 10:05] = 180 s ; Ezra : [10:02 -> 10:07] (window_end) = 300 s
check(used_total == 120 + 180 + 300,
      "34 pas de double débit : Séléna plafonne à 300 s (grâce), Ezra facturé sa fenêtre")
check(DB["consultations"][CID_SEL]["billed_until"] == T(10, 5, 0),
      "34b Séléna : facturée jusqu'à window_end (10:05), jamais au-delà")

# 35. retry /flow au même `now` ne redébite pas
used_before = DB["allowance"][0]["monthly_used_seconds"]
_flow(CID_EZR, T(10, 10, 0))
check(DB["allowance"][0]["monthly_used_seconds"] == used_before,
      "35 rejeu au même instant : 0 s débitée (idempotent)")

# 36. inactivité > 300 s jamais facturée au-delà de 300
reset_db()
seed_account(UID, first_free=0)
seed_allowance(UID, used_seconds=0)
seed_consultation(CID_SEL, "selena", T(10, 0, 0),
                  last_activity_at=T(10, 0, 0), billed_until=T(10, 0, 0))
seed_consultation(CID_EZR, "ezra", T(10, 1, 0))
_flow(CID_EZR, T(11, 0, 0))    # 1 h plus tard
check(DB["allowance"][0]["monthly_used_seconds"] == 300,
      "36 Séléna inactive 1 h -> exactement 300 s facturées (grâce), pas 3600")

# 37. deux anciennes fenêtres ouvertes -> settle borné pour chacune
reset_db()
seed_account(UID, first_free=0)
seed_allowance(UID, used_seconds=0)
seed_consultation(CID_SEL, "selena", T(10, 0, 0),
                  last_activity_at=T(10, 0, 0), billed_until=T(10, 0, 0))
seed_consultation("cccccccc-0000-4000-8000-00000000000c", "luna", T(10, 0, 30),
                  last_activity_at=T(10, 0, 30), billed_until=T(10, 0, 30))
seed_consultation(CID_EZR, "ezra", T(10, 2, 0))
_flow(CID_EZR, T(10, 30, 0))
# Séléna 300 s + Luna 300 s = 600 s ; Ezra ouvre sa fenêtre (0 s)
check(DB["allowance"][0]["monthly_used_seconds"] == 600,
      "37 deux fenêtres anciennes -> 300 s chacune (bornées), somme 600 s")

# 38. total restant jamais négatif
reset_db()
seed_account(UID, first_free=50, purchased=0)
seed_allowance(UID, allowance_seconds=0, used_seconds=0)
seed_consultation(CID_SEL, "selena", T(10, 0, 0),
                  last_activity_at=T(10, 0, 0), billed_until=T(10, 0, 0))
seed_consultation(CID_EZR, "ezra", T(10, 1, 0))
res = _flow(CID_EZR, T(10, 10, 0))
check(res["status"] == "time_exhausted"
      and res["time"]["total_remaining_seconds"] == 0
      and DB["accounts"][UID]["first_free_seconds_remaining"] == 0,
      "38 portefeuille épuisé -> total 0 (jamais négatif), 402 time_exhausted")

# 39. ordre des buckets inchangé (first_free avant premium)
reset_db()
seed_account(UID, first_free=200, purchased=0)
seed_allowance(UID, allowance_seconds=28800, used_seconds=0)
seed_consultation(CID_SEL, "selena", T(10, 0, 0),
                  last_activity_at=T(10, 0, 0), billed_until=T(10, 0, 0))
seed_consultation(CID_EZR, "ezra", T(10, 1, 0))
_flow(CID_EZR, T(10, 2, 0))   # 120 s dus
check(DB["accounts"][UID]["first_free_seconds_remaining"] == 80
      and DB["allowance"][0]["monthly_used_seconds"] == 0,
      "39 débit : first_free consommé EN PREMIER (200->80), premium intact")

# 40. GET /list n'a aucun effet de facturation
tok = _fresh(first_free=1000)
seed_consultation(CID_SEL, "selena", T(10, 0),
                  last_activity_at=T(10, 0), billed_until=T(10, 0))
before = DB["accounts"][UID]["first_free_seconds_remaining"]
client.get("/api/consultation/list", headers=_hdr(tok))
check(DB["accounts"][UID]["first_free_seconds_remaining"] == before,
      "40 GET /list ne débite rien")


print("=" * 64)
print("J6-B2 — F. GET /api/consultation/state (sans cutoff expires_at)")
print("=" * 64)

# 41. /state renvoie le dernier fil même si expires_at < now
tok = _fresh()
old = T(1, 0)
seed_consultation(CID_SEL, "selena", old)
DB["consultations"][CID_SEL]["expires_at"] = old + timedelta(hours=2)  # passé
j = client.get("/api/consultation/state", headers=_hdr(tok)).get_json()
check(j["consultation"] is not None and j["consultation"]["id"] == CID_SEL,
      "41 /state renvoie encore la consultation après 2 h (aucun cutoff expires_at)")

# 42. time snapshot correct (first_free 3600 + premium 28800)
check(j["time"]["first_free_remaining_seconds"] == 3600
      and j["time"]["premium_remaining_seconds"] == 28800
      and j["time"]["total_remaining_seconds"] == 3600 + 28800,
      "42 /state : snapshot temps (portefeuille global) correct")

# 43. /state ne détruit aucun fil
tok = _fresh()
seed_consultation(CID_SEL, "selena", T(10, 0))
seed_consultation(CID_EZR, "ezra", T(11, 0))
client.get("/api/consultation/state", headers=_hdr(tok))
check(len(DB["consultations"]) == 2,
      "43 GET /state ne supprime / ne ferme aucun fil")


# ---------------------------------------------------------------------------
print()
_total = _STATE["pass"] + _STATE["fail"]
if _STATE["fail"] == 0:
    print(f"OK  {_STATE['pass']}/{_total} tests passés")
    sys.exit(0)
else:
    print(f"XX  {_STATE['fail']} test(s) en échec sur {_total}")
    sys.exit(1)
