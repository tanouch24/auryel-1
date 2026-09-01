"""
test_app_consultation.py — B4.2 : câblage du moteur de consultations 2 h dans
l'API mobile (POST /api/consultation/message + GET /api/consultation/state).

100 % local : psycopg2 mocké, get_conn -> fausse DB en mémoire (tables app +
moteur B4.1), call_llm mocké, aucun réseau. Style aligné sur test_app_chat.py.
"""

import sys
from datetime import timedelta
from unittest.mock import MagicMock, patch

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
# Fausse base de données en mémoire (tables app + moteur consultations B4.1)
# ---------------------------------------------------------------------------
FAKE = {"accounts": [], "app_profiles": [], "app_sessions": [], "messages": [],
        "consultations": [], "consultation_allowance": [], "earned_credits": [],
        "seq": [0]}


def reset_db():
    for key in ("accounts", "app_profiles", "app_sessions", "messages",
                "consultations", "consultation_allowance", "earned_credits"):
        FAKE[key].clear()
    FAKE["seq"][0] = 0


def seed_account(user_id, deleted_at=None, email="u@example.com",
                 first_consultation_used_at=None,
                 first_free_seconds_remaining="__backfill__",
                 purchased_seconds_remaining=0):
    # first_free_seconds_remaining : miroir du backfill migration v34
    # (3600 si la gratuite n'a jamais été consommée, 0 sinon), sauf override.
    if first_free_seconds_remaining == "__backfill__":
        first_free_seconds_remaining = (
            3600 if first_consultation_used_at is None else 0)
    FAKE["accounts"].append({
        "user_id": user_id, "email": email, "deleted_at": deleted_at,
        "first_consultation_used_at": first_consultation_used_at,
        "first_free_seconds_remaining": first_free_seconds_remaining,
        "purchased_seconds_remaining": purchased_seconds_remaining,
    })


_APP_SELECT = "SELECT " + ", ".join(A._APP_PROFILE_FIELDS) + " FROM app_profiles WHERE user_id=%s"
_APP_INSERT = ("INSERT INTO app_profiles (user_id, guide, onboarding_done, date_premier_contact, "
               "date_dernier_contact) VALUES (%s, 'selena', FALSE, %s, %s) ON CONFLICT (user_id) DO NOTHING")


def _blank_profile(uid):
    row = {f: "" for f in A._APP_PROFILE_FIELDS}
    row["user_id"] = uid
    row["guide"] = "selena"
    for c in ("nb_echanges", "nb_echanges_decouverte", "nb_echanges_dernier_tirage",
              "nb_echanges_dernier_psaume"):
        row[c] = 0
    row["tirage_propose_en_attente"] = False
    row["onboarding_done"] = False
    return row


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
        k = " ".join(sql.split())
        p = params
        self.rowcount = -1
        self._result = None
        self._rows = None

        # ---- accounts ----
        if k == "SELECT deleted_at FROM accounts WHERE user_id=%s":
            (uid,) = p
            r = next((a for a in FAKE["accounts"] if a["user_id"] == uid), None)
            self._result = (r["deleted_at"],) if r else None
        elif k == "SELECT email FROM accounts WHERE user_id=%s":
            (uid,) = p
            r = next((a for a in FAKE["accounts"] if a["user_id"] == uid), None)
            self._result = (r["email"],) if r else None

        # ---- app_sessions ----
        elif k == ("INSERT INTO app_sessions (id, user_id, token_hash, created_at, expires_at, "
                   "platform, device_label) VALUES (%s, %s, %s, %s, %s, %s, %s)"):
            sid, uid, th, ca, ea, pf, dl = p
            FAKE["app_sessions"].append({"id": sid, "user_id": uid, "token_hash": th,
                                         "created_at": ca, "expires_at": ea, "revoked_at": None,
                                         "last_used_at": None, "platform": pf, "device_label": dl})
        elif k == ("SELECT s.id, s.user_id, s.expires_at, s.revoked_at, a.email, a.deleted_at "
                   "FROM app_sessions s JOIN accounts a ON a.user_id = s.user_id "
                   "WHERE s.token_hash=%s"):
            (th,) = p
            s = next((x for x in FAKE["app_sessions"] if x["token_hash"] == th), None)
            if not s:
                self._result = None
            else:
                a = next((x for x in FAKE["accounts"] if x["user_id"] == s["user_id"]), None)
                self._result = ((s["id"], s["user_id"], s["expires_at"], s["revoked_at"],
                                 a["email"], a["deleted_at"]) if a else None)
        elif k == "UPDATE app_sessions SET last_used_at=%s WHERE id=%s":
            ts, sid = p
            for x in FAKE["app_sessions"]:
                if x["id"] == sid:
                    x["last_used_at"] = ts

        # ---- app_profiles ----
        elif k == _APP_SELECT:
            (uid,) = p
            r = next((x for x in FAKE["app_profiles"] if x["user_id"] == uid), None)
            self._result = tuple(r[f] for f in A._APP_PROFILE_FIELDS) if r else None
        elif k == _APP_INSERT:
            uid, dpc, ddc = p
            if not any(x["user_id"] == uid for x in FAKE["app_profiles"]):
                row = _blank_profile(uid)
                row["date_premier_contact"] = dpc
                row["date_dernier_contact"] = ddc
                FAKE["app_profiles"].append(row)
        elif k.startswith("UPDATE app_profiles SET ") and k.endswith(" WHERE user_id=%s"):
            set_part = k[len("UPDATE app_profiles SET "):-len(" WHERE user_id=%s")]
            cols = [seg.split("=")[0].strip() for seg in set_part.split(",")]
            uid = p[-1]
            vals = list(p[:-1])
            for r in FAKE["app_profiles"]:
                if r["user_id"] == uid:
                    for col, val in zip(cols, vals):
                        r[col] = val

        # ---- messages (app) ----
        elif k == ("INSERT INTO messages (user_id, phone, role, content, timestamp, consultation_id) "
                   "VALUES (%s, NULL, %s, %s, %s, %s)"):
            uid, role, content, ts, consultation_id = p
            FAKE["seq"][0] += 1
            FAKE["messages"].append({"id": FAKE["seq"][0], "user_id": uid, "phone": None,
                                     "role": role, "content": content, "timestamp": ts,
                                     "consultation_id": consultation_id})
        elif k == "SELECT role,content FROM messages WHERE user_id=%s ORDER BY id DESC LIMIT %s":
            uid, limit = p
            rows = sorted([m for m in FAKE["messages"] if m["user_id"] == uid],
                          key=lambda m: m["id"], reverse=True)
            self._rows = [(m["role"], m["content"]) for m in rows[:limit]]
        elif k == ("SELECT role, content, timestamp FROM messages "
                   "WHERE user_id=%s AND consultation_id=%s AND role IN ('user','assistant') "
                   "ORDER BY timestamp ASC, id ASC"):
            uid, cid = p
            rows = [m for m in FAKE["messages"]
                    if m["user_id"] == uid and m["consultation_id"] == cid
                    and m["role"] in ("user", "assistant")]
            rows.sort(key=lambda m: (m["timestamp"], m["id"]))
            self._rows = [(m["role"], m["content"], m["timestamp"]) for m in rows]

        # ---- moteur consultations 2 h / crédits (B4.1) ----
        elif k == ("SELECT user_id, first_consultation_used_at FROM accounts "
                   "WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE"):
            (uid,) = p
            r = next((a for a in FAKE["accounts"]
                      if a["user_id"] == uid and a["deleted_at"] is None), None)
            self._result = (uid, r.get("first_consultation_used_at")) if r else None

        # ---- flux transactionnel TEMPS (TIMER-A.3c-2b/2c) ----
        elif k == ("SELECT user_id FROM accounts "
                   "WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE"):
            (uid,) = p
            r = next((a for a in FAKE["accounts"]
                      if a["user_id"] == uid and a["deleted_at"] is None), None)
            self._result = (uid,) if r else None

        elif k == ("SELECT id, last_activity_at FROM consultations "
                   "WHERE user_id=%s ORDER BY started_at DESC LIMIT 1"):
            (uid,) = p
            rows = sorted([c for c in FAKE["consultations"] if c["user_id"] == uid],
                          key=lambda c: c["started_at"], reverse=True)
            if rows:
                self._result = (rows[0]["id"], rows[0].get("last_activity_at"))

        # ---- GET /api/consultation/messages (TIMER-A.3d) : lecture seule ABSOLUE,
        # consultation la plus récente, SANS aucun cutoff temporel.
        elif k == ("SELECT id FROM consultations "
                   "WHERE user_id=%s ORDER BY started_at DESC LIMIT 1"):
            (uid,) = p
            rows = sorted([c for c in FAKE["consultations"] if c["user_id"] == uid],
                          key=lambda c: c["started_at"], reverse=True)
            if rows:
                self._result = (rows[0]["id"],)

        elif k == ("SELECT id, advisor_id, last_activity_at, billed_until FROM consultations "
                   "WHERE user_id=%s ORDER BY started_at DESC LIMIT 1"):
            (uid,) = p
            rows = sorted([c for c in FAKE["consultations"] if c["user_id"] == uid],
                          key=lambda c: c["started_at"], reverse=True)
            if rows:
                c0 = rows[0]
                self._result = (c0["id"], c0["advisor_id"],
                                c0.get("last_activity_at"), c0.get("billed_until"))

        elif k == ("INSERT INTO consultations (id, user_id, advisor_id, started_at, expires_at, "
                   "credit_source, created_at) VALUES (%s, %s, %s, %s, %s, 'time', %s)"):
            cid, uid, advisor, started, expires, created = p
            FAKE["consultations"].append({
                "id": str(cid), "user_id": str(uid), "advisor_id": advisor,
                "started_at": started, "expires_at": expires,
                "credit_source": "time", "created_at": created,
                "last_activity_at": None, "billed_until": None,
            })
            self.rowcount = 1

        elif k == ("SELECT started_at, expires_at, credit_source FROM consultations "
                   "WHERE id=%s"):
            (cid,) = p
            c0 = next((c for c in FAKE["consultations"] if c["id"] == str(cid)), None)
            if c0 is not None:
                self._result = (c0["started_at"], c0["expires_at"], c0["credit_source"])

        elif k == "UPDATE consultations SET last_activity_at=%s WHERE id=%s":
            la, cid = p
            c0 = next((c for c in FAKE["consultations"] if c["id"] == str(cid)), None)
            if c0 is not None:
                c0["last_activity_at"] = la
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k == ("UPDATE accounts SET first_consultation_used_at=%s "
                   "WHERE user_id=%s AND first_consultation_used_at IS NULL"):
            ts, uid = p
            r = next((a for a in FAKE["accounts"] if a["user_id"] == str(uid)), None)
            if r is not None and r.get("first_consultation_used_at") is None:
                r["first_consultation_used_at"] = ts
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k == "SELECT first_consultation_used_at FROM accounts WHERE user_id=%s":
            (uid,) = p
            r = next((a for a in FAKE["accounts"] if a["user_id"] == str(uid)), None)
            self._result = (r.get("first_consultation_used_at"),) if r is not None else None

        elif k == ("SELECT id, advisor_id, started_at, expires_at, credit_source "
                   "FROM consultations WHERE user_id=%s AND expires_at > %s "
                   "ORDER BY started_at DESC LIMIT 1"):
            uid, now = p
            rows = [c for c in FAKE["consultations"]
                    if c["user_id"] == uid and c["expires_at"] > now]
            rows.sort(key=lambda c: c["started_at"], reverse=True)
            if rows:
                c0 = rows[0]
                self._result = (c0["id"], c0["advisor_id"], c0["started_at"],
                                c0["expires_at"], c0["credit_source"])

        elif k == ("SELECT period_start, monthly_limit, monthly_used "
                   "FROM consultation_allowance WHERE user_id=%s AND period_start <= %s "
                   "AND period_end > %s ORDER BY period_start DESC LIMIT 1 FOR UPDATE"):
            uid, now, _n2 = p
            rows = [a for a in FAKE["consultation_allowance"]
                    if a["user_id"] == uid and a["period_start"] <= now and a["period_end"] > now]
            rows.sort(key=lambda a: a["period_start"], reverse=True)
            if rows:
                a0 = rows[0]
                self._result = (a0["period_start"], a0["monthly_limit"], a0["monthly_used"])

        elif k == ("UPDATE consultation_allowance SET monthly_used = monthly_used + 1 "
                   "WHERE user_id=%s AND period_start=%s"):
            uid, ps = p
            r = next((a for a in FAKE["consultation_allowance"]
                      if a["user_id"] == uid and a["period_start"] == ps), None)
            if r is not None:
                r["monthly_used"] += 1
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k == ("SELECT id, source FROM earned_credits WHERE user_id=%s AND consumed_at IS NULL "
                   "ORDER BY granted_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"):
            (uid,) = p
            rows = [e for e in FAKE["earned_credits"]
                    if e["user_id"] == uid and e["consumed_at"] is None]
            rows.sort(key=lambda e: e["granted_at"])
            if rows:
                self._result = (rows[0]["id"], rows[0]["source"])

        elif k == ("INSERT INTO consultations (id, user_id, advisor_id, started_at, expires_at, "
                   "credit_source, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"):
            cid, uid, advisor, started, expires, src, created = p
            FAKE["consultations"].append({
                "id": str(cid), "user_id": str(uid), "advisor_id": advisor,
                "started_at": started, "expires_at": expires,
                "credit_source": src, "created_at": created,
                # colonnes moteur temps (migration v34) : NULL à l'INSERT.
                "last_activity_at": None, "billed_until": None,
            })
            self.rowcount = 1

        elif k == "UPDATE earned_credits SET consumed_at=%s, consultation_id=%s WHERE id=%s":
            consumed, cons_id, eid = p
            r = next((e for e in FAKE["earned_credits"] if e["id"] == str(eid)), None)
            if r is not None:
                r["consumed_at"] = consumed
                r["consultation_id"] = str(cons_id)
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k == ("SELECT period_start, period_end, monthly_limit, monthly_used "
                   "FROM consultation_allowance WHERE user_id=%s AND period_start <= %s "
                   "AND period_end > %s ORDER BY period_start DESC LIMIT 1"):
            uid, now, _n2 = p
            rows = [a for a in FAKE["consultation_allowance"]
                    if a["user_id"] == uid and a["period_start"] <= now and a["period_end"] > now]
            rows.sort(key=lambda a: a["period_start"], reverse=True)
            if rows:
                a0 = rows[0]
                self._result = (a0["period_start"], a0["period_end"],
                                a0["monthly_limit"], a0["monthly_used"])

        elif k == "SELECT COUNT(*) FROM earned_credits WHERE user_id=%s AND consumed_at IS NULL":
            (uid,) = p
            n = sum(1 for e in FAKE["earned_credits"]
                    if e["user_id"] == uid and e["consumed_at"] is None)
            self._result = (n,)

        elif k == ("INSERT INTO consultation_allowance (user_id, period_start, period_end, "
                   "monthly_limit, monthly_used, created_at, source_subscription_id, "
                   "source_period_start) VALUES (%s, %s, %s, %s, 0, %s, %s, %s) "
                   "ON CONFLICT (user_id, period_start) DO NOTHING"):
            uid, ps, pe, lim, created, src_sub, src_ps = p
            if any(a for a in FAKE["consultation_allowance"]
                   if a["user_id"] == str(uid) and a["period_start"] == ps):
                self.rowcount = 0
            else:
                FAKE["consultation_allowance"].append({
                    "user_id": str(uid), "period_start": ps, "period_end": pe,
                    "monthly_limit": lim, "monthly_used": 0, "created_at": created,
                    "source_subscription_id": src_sub, "source_period_start": src_ps,
                    # DEFAULT migration v34 : 8 h Premium / période, 0 consommée.
                    "monthly_allowance_seconds": 28800, "monthly_used_seconds": 0,
                })
                self.rowcount = 1

        elif k == ("INSERT INTO earned_credits (id, user_id, source, granted_at, consumed_at, "
                   "consultation_id, metadata) VALUES (%s, %s, %s, %s, NULL, NULL, %s)"):
            eid, uid, src, granted, meta = p
            FAKE["earned_credits"].append({
                "id": str(eid), "user_id": str(uid), "source": src, "granted_at": granted,
                "consumed_at": None, "consultation_id": None, "metadata": meta,
            })
            self.rowcount = 1

        # ---- moteur TEMPS (TIMER-A.2) + câblage GET /state (TIMER-A.3a) ----
        elif k == ("SELECT id, advisor_id, started_at, expires_at, credit_source, "
                   "last_activity_at, billed_until FROM consultations "
                   "WHERE user_id=%s AND expires_at > %s ORDER BY started_at DESC LIMIT 1"):
            uid, now = p
            rows = [c for c in FAKE["consultations"]
                    if c["user_id"] == uid and c["expires_at"] > now]
            rows.sort(key=lambda c: c["started_at"], reverse=True)
            if rows:
                c0 = rows[0]
                self._result = (c0["id"], c0["advisor_id"], c0["started_at"],
                                c0["expires_at"], c0["credit_source"],
                                c0.get("last_activity_at"), c0.get("billed_until"))

        elif k == ("SELECT user_id, last_activity_at, billed_until "
                   "FROM consultations WHERE id=%s"):
            (cid,) = p
            c0 = next((c for c in FAKE["consultations"] if c["id"] == str(cid)), None)
            self._result = ((c0["user_id"], c0.get("last_activity_at"),
                             c0.get("billed_until")) if c0 else None)

        elif k == "UPDATE consultations SET billed_until=%s WHERE id=%s":
            bu, cid = p
            c0 = next((c for c in FAKE["consultations"] if c["id"] == str(cid)), None)
            if c0 is not None:
                c0["billed_until"] = bu
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k == ("UPDATE consultations SET last_activity_at=%s, billed_until=%s "
                   "WHERE id=%s"):
            la, bu, cid = p
            c0 = next((c for c in FAKE["consultations"] if c["id"] == str(cid)), None)
            if c0 is not None:
                c0["last_activity_at"] = la
                c0["billed_until"] = bu
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k in ("SELECT first_free_seconds_remaining, purchased_seconds_remaining "
                   "FROM accounts WHERE user_id=%s",
                   "SELECT first_free_seconds_remaining, purchased_seconds_remaining "
                   "FROM accounts WHERE user_id=%s FOR UPDATE"):
            (uid,) = p
            a = next((x for x in FAKE["accounts"] if x["user_id"] == str(uid)), None)
            self._result = ((a.get("first_free_seconds_remaining"),
                             a.get("purchased_seconds_remaining")) if a else None)

        elif k in ("SELECT monthly_allowance_seconds, monthly_used_seconds "
                   "FROM consultation_allowance WHERE user_id=%s AND period_start <= %s "
                   "AND period_end > %s ORDER BY period_start DESC LIMIT 1",
                   "SELECT period_start, monthly_allowance_seconds, monthly_used_seconds "
                   "FROM consultation_allowance WHERE user_id=%s AND period_start <= %s "
                   "AND period_end > %s ORDER BY period_start DESC LIMIT 1 FOR UPDATE"):
            uid, now, _n2 = p
            rows = [a for a in FAKE["consultation_allowance"]
                    if a["user_id"] == str(uid)
                    and a["period_start"] <= now and a["period_end"] > now]
            rows.sort(key=lambda a: a["period_start"], reverse=True)
            if rows:
                a0 = rows[0]
                _al = a0.get("monthly_allowance_seconds", 28800)
                _us = a0.get("monthly_used_seconds", 0)
                if k.startswith("SELECT period_start,"):
                    self._result = (a0["period_start"], _al, _us)
                else:
                    self._result = (_al, _us)

        elif k == ("SELECT period_start, period_end FROM consultation_allowance "
                   "WHERE user_id=%s AND period_start <= %s AND period_end > %s "
                   "ORDER BY period_start DESC LIMIT 1"):
            uid, now, _n2 = p
            rows = [a for a in FAKE["consultation_allowance"]
                    if a["user_id"] == str(uid)
                    and a["period_start"] <= now and a["period_end"] > now]
            rows.sort(key=lambda a: a["period_start"], reverse=True)
            if rows:
                self._result = (rows[0]["period_start"], rows[0]["period_end"])

        elif k == ("UPDATE accounts SET first_free_seconds_remaining=%s, "
                   "purchased_seconds_remaining=%s WHERE user_id=%s"):
            ff, pu, uid = p
            a = next((x for x in FAKE["accounts"] if x["user_id"] == str(uid)), None)
            if a is not None:
                a["first_free_seconds_remaining"] = ff
                a["purchased_seconds_remaining"] = pu
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k == ("UPDATE consultation_allowance SET monthly_used_seconds=%s "
                   "WHERE user_id=%s AND period_start=%s"):
            used, uid, ps = p
            a = next((x for x in FAKE["consultation_allowance"]
                      if x["user_id"] == str(uid) and x["period_start"] == ps), None)
            if a is not None:
                a["monthly_used_seconds"] = used
                self.rowcount = 1
            else:
                self.rowcount = 0

        else:
            raise AssertionError("SQL non géré par le fake B4.2 : " + k)


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
A.app.config["RATELIMIT_ENABLED"] = False
A.limiter.enabled = False
client = A.app.test_client()

UID1 = "11111111-1111-4111-8111-111111111111"
UID2 = "22222222-2222-4222-8222-222222222222"
REPLY = "Je te vois clairement, et un mouvement se dessine autour de toi."


def _seed_premium(uid, monthly_limit=4):
    now = A._utcnow()
    A.provision_allowance(uid, now - timedelta(days=1), now + timedelta(days=29),
                          monthly_limit=monthly_limit)


# Marque "première gratuite DÉJÀ consommée" : par défaut les scénarios existants
# testent le quota Premium / les earned credits comme 1re source. Passer
# first_free=True pour un compte neuf avec sa gratuite disponible.
_FF_USED = A._utcnow() - timedelta(days=2)


def fresh(uid=UID1, premium=True, monthly_limit=4, first_free=False):
    reset_db()
    seed_account(uid, first_consultation_used_at=None if first_free else _FF_USED)
    if premium:
        _seed_premium(uid, monthly_limit=monthly_limit)
    return A.create_app_session(uid)


def _post(tok, message="j'ai besoin d'y voir clair"):
    return client.post("/api/consultation/message", json={"message": message},
                       headers={"Authorization": f"Bearer {tok}"})


print("=" * 64)
print("TEST APP CONSULTATION — B4.2  (route message + route state)")
print("=" * 64)

# ===========================================================================
# POST /api/consultation/message — CONTRAT MOTEUR TEMPS (TIMER-A.3c-2c)
# Le POST est passé sur _open_time_consultation_flow_tx : plus de session 2 h,
# plus de monthly_used += 1, plus de consommation d'earned_credit. Le temps est
# débité seconde par seconde (settle de la fenêtre 300 s), waterfall
# first_free -> premium -> purchased. credit_source d'une consultation temps
# = "time". 402 -> error "time_exhausted".
# ===========================================================================

def _expire_window(uid=UID1):
    """Ferme la fenêtre d'activité (last_activity_at loin dans le passé). Dans le
    modèle TEMPS c'est la fenêtre 300 s — pas expires_at — qui décide de la
    reprise / réouverture d'une consultation logique."""
    past = A._utcnow() - timedelta(hours=1)
    for c in FAKE["consultations"]:
        if c["user_id"] == uid:
            c["last_activity_at"] = past
            c["billed_until"] = past


def _drain_first_free(uid=UID1):
    for a in FAKE["accounts"]:
        if a["user_id"] == uid:
            a["first_free_seconds_remaining"] = 0


# --- 1. premier message Premium : ouverture consultation TEMPS + LLM + messages
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY) as m_llm, \
     patch("auryel_bot.random.random", return_value=1.0):
    r = _post(tok)
j = r.get_json()
check(r.status_code == 200, "1a premier message Premium -> 200")
check(j["reply"] == REPLY and m_llm.call_count == 1, "1b reply renvoyé, call_llm appelé 1x")
check(set(j.keys()) == {"reply", "consultation", "time", "quota"},
      "1c racine = reply / consultation / time / quota")
check(j["consultation"]["opened_now"] is True, "1d opened_now = true (flow.consultation.created)")
check(j["consultation"]["credit_source"] == "time", "1e credit_source = time")
check(j["consultation"]["advisor_id"] == "selena", "1f advisor_id = selena (guide du profil)")
check(j["time"]["premium_remaining_seconds"] == 28800
      and j["time"]["total_remaining_seconds"] == 28800,
      "1g time : Premium plein (1er touch ne débite rien)")
check(j["consultation"]["seconds_remaining"] == j["time"]["total_remaining_seconds"] == 28800,
      "1h consultation.seconds_remaining == time.total_remaining_seconds (PAS expires_at - now)")
check(j["quota"]["is_premium"] is True and j["quota"]["monthly_limit"] == 8
      and j["quota"]["monthly_used"] == 0,
      "1i shim : is_premium, monthly_limit 8, monthly_used 0 (aucun débit legacy)")
check(FAKE["consultation_allowance"][0]["monthly_used"] == 0,
      "1j monthly_used legacy JAMAIS incrémenté par le POST")
_cid = j["consultation"]["id"]
_msgs = FAKE["messages"]
check(any(m["role"] == "user" and m["content"] == "j'ai besoin d'y voir clair"
          and m["consultation_id"] == _cid for m in _msgs),
      "1k message user rattaché à consultation_id")
check(any(m["role"] == "assistant" and m["content"] == REPLY
          and m["consultation_id"] == _cid for m in _msgs),
      "1l message assistant rattaché au MÊME consultation_id")
check("no-store" in r.headers.get("Cache-Control", ""), "1m Cache-Control: no-store")
_JSON_FIRST_FREE = None
_JSON_PREMIUM = dict(j)          # §28 B — succès Premium
_JSON_PURCHASED = None
_JSON_402 = None

# --- 2. deuxième message < 5 min : même consultation logique, opened_now=false
with patch.object(A, "call_llm", return_value=REPLY):
    r2 = _post(tok, "et ensuite ?")
j2 = r2.get_json()
check(r2.status_code == 200 and j2["consultation"]["id"] == _cid,
      "2a 2e message < 5 min -> même consultation logique")
check(j2["consultation"]["opened_now"] is False, "2b opened_now = false")
check(FAKE["consultation_allowance"][0]["monthly_used"] == 0,
      "2c monthly_used legacy toujours 0 (aucun débit d'unité)")
check(len(FAKE["consultations"]) == 1, "2d une seule consultation en base (pas de duplication)")
check(all(m["consultation_id"] == _cid for m in FAKE["messages"]),
      "2e tous les messages de la session portent le même consultation_id")
check(isinstance(j2["consultation"]["seconds_remaining"], int)
      and j2["consultation"]["seconds_remaining"] == j2["time"]["total_remaining_seconds"],
      "2f seconds_remaining = time.total_remaining_seconds")

# --- 3. aucun temps -> 402 time_exhausted, aucun effet de bord -----------------
tok = fresh(premium=False)           # first_free consommée, pas de Premium, pas de purchased
with patch.object(A, "call_llm", return_value=REPLY) as m_llm3, \
     patch.object(A, "_app_persist_emotional_context") as m_emo:
    r3 = _post(tok)
j3 = r3.get_json()
check(r3.status_code == 402, "3a aucun temps -> HTTP 402")
check(j3["error"] == "time_exhausted" and j3["consultation"] is None,
      "3b payload { error: time_exhausted, consultation: null }")
check(j3["time"]["total_remaining_seconds"] == 0
      and j3["time"]["window_active"] is False
      and j3["time"]["window_expires_at"] is None,
      "3c time = 0, fenêtre inactive")
check(set(j3["quota"].keys()) == {"is_premium", "monthly_limit", "monthly_used",
                                   "monthly_remaining", "earned_available",
                                   "first_free_available", "period_start", "period_end"},
      "3d quota shim présent sur le 402")
check(m_llm3.call_count == 0, "3e call_llm jamais appelé")
check(m_emo.call_count == 0, "3f _app_persist_emotional_context jamais appelé")
check(len(FAKE["messages"]) == 0 and len(FAKE["consultations"]) == 0,
      "3g aucun message, aucune consultation créés")
check("no-store" in r3.headers.get("Cache-Control", ""), "3h 402 -> Cache-Control: no-store")
_JSON_402 = dict(j3)            # §28 D — 402 time_exhausted

# --- 4. earned credit NE DÉBLOQUE PLUS l'accès (modèle temps) -----------------
tok = fresh(premium=False)
A.grant_earned_credit(UID1, "referral")
with patch.object(A, "call_llm", return_value=REPLY) as m_llm4:
    r4 = _post(tok)
j4 = r4.get_json()
check(r4.status_code == 402 and j4["error"] == "time_exhausted",
      "4a earned credit seul ne donne plus accès -> 402 time_exhausted")
check(m_llm4.call_count == 0, "4b aucun LLM")
check(sum(1 for e in FAKE["earned_credits"] if e["consumed_at"] is None) == 1
      and all(e["consumed_at"] is None for e in FAKE["earned_credits"]),
      "4c earned credit JAMAIS consommé par le nouveau POST (aucun consumed_at)")
check(j4["quota"]["earned_available"] == 1, "4d quota.earned_available préservé (1)")

# --- 5. gratuit sans aucun temps -> 402 -------------------------------------
tok = fresh(premium=False)
with patch.object(A, "call_llm", return_value=REPLY) as m_llm5:
    r5 = _post(tok)
check(r5.status_code == 402 and r5.get_json()["error"] == "time_exhausted"
      and m_llm5.call_count == 0,
      "5 gratuit sans temps -> 402 time_exhausted, aucun LLM")

# --- 6. advisor RÉEL figé pendant la fenêtre active : le LLM parle avec lui ----
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY) as m_llm6a, \
     patch("auryel_bot.random.random", return_value=1.0):
    _post(tok, "bonjour selena")
_sys_a = m_llm6a.call_args[0][0][0]["content"]
A.update_app_profile(UID1, guide="maia")          # l'app change de conseiller préféré
with patch.object(A, "call_llm", return_value=REPLY) as m_llm6b, \
     patch("auryel_bot.random.random", return_value=1.0):
    r6 = _post(tok, "et là ?")
_sys_b = m_llm6b.call_args[0][0][0]["content"]
check(A.GUIDES["selena"].get("nom", "selena") in _sys_a
      and A.GUIDES["selena"].get("nom", "selena") in _sys_b
      and A.GUIDES["maia"].get("nom", "maia") not in _sys_b,
      "6a fenêtre active : LLM parle avec l'advisor RÉEL (selena) sur les 2 tours malgré guide=maia")
check(r6.get_json()["consultation"]["advisor_id"] == "selena",
      "6b consultation.advisor_id = selena (figé)")
check(A.get_app_profile(UID1)["guide"] == "maia",
      "6c app_profiles.guide vaut maia (jamais réécrit par la route)")
check(len(FAKE["consultations"]) == 1,
      "6d aucune nouvelle consultation (changement d'advisor ignoré fenêtre active)")

# --- 7. GET /api/consultation/state — TIMER-A.3a (bloc time + shim quota) -----
def _state(tok):
    return client.get("/api/consultation/state",
                      headers={"Authorization": f"Bearer {tok}"}).get_json()


def _set_time_meter(cid, last_activity_at, billed_until):
    """Simule ce que _touch câblera en A.3c : pose les curseurs de fenêtre."""
    c0 = next(c for c in FAKE["consultations"] if c["id"] == cid)
    c0["last_activity_at"] = last_activity_at
    c0["billed_until"] = billed_until


# 7a. inactif : aucune consultation, Premium actif -> time + shim.
tok = fresh()
js = _state(tok)
check(js["consultation"] is None, "7a state : consultation = null si inactif")
check(js["time"]["premium_remaining_seconds"] == 28800
      and js["time"]["total_remaining_seconds"] == 28800
      and js["time"]["window_active"] is False
      and js["time"]["window_expires_at"] is None,
      "7b state : time = 28800 Premium, fenêtre inactive")
check(js["quota"]["is_premium"] is True and js["quota"]["monthly_limit"] == 8
      and js["quota"]["monthly_remaining"] == 8 and js["quota"]["monthly_used"] == 0,
      "7b2 shim : monthly_limit 8, remaining 8, used 0")
check(len(FAKE["consultations"]) == 0, "7c state n'ouvre AUCUNE consultation")

# 7d. actif après un message + earned credit préservé.
with patch.object(A, "call_llm", return_value=REPLY):
    _post(tok, "on commence")
A.grant_earned_credit(UID1, "share_30d")
js = _state(tok)
check(js["consultation"] is not None and js["consultation"]["advisor_id"] == "selena",
      "7d state : consultation courante renvoyée")
check(js["consultation"]["seconds_remaining"] == js["time"]["total_remaining_seconds"]
      == 28800,
      "7e state : consultation.seconds_remaining == time.total_remaining_seconds (plus un countdown 2 h)")
check(js["quota"]["earned_available"] == 1 and js["quota"]["monthly_used"] == 0,
      "7f state : earned_credits legacy préservé (1) ; shim monthly_used=0 (aucune SECONDE débitée)")
_snap = (len(FAKE["consultations"]),
         FAKE["consultation_allowance"][0]["monthly_used"],
         FAKE["consultation_allowance"][0]["monthly_used_seconds"],
         FAKE["accounts"][0]["first_free_seconds_remaining"],
         sum(1 for e in FAKE["earned_credits"] if e["consumed_at"] is None))
_state(tok)
check((len(FAKE["consultations"]),
       FAKE["consultation_allowance"][0]["monthly_used"],
       FAKE["consultation_allowance"][0]["monthly_used_seconds"],
       FAKE["accounts"][0]["first_free_seconds_remaining"],
       sum(1 for e in FAKE["earned_credits"] if e["consumed_at"] is None)) == _snap,
      "7g deux appels state (last_activity NULL) : rien consommé, aucun monthly_used legacy incrémenté")

# --- 7.A/B/C/D — snapshot par bucket ---------------------------------------
tok = fresh(premium=False, first_free=True)          # nouveau compte
check(_state(tok)["time"]["first_free_remaining_seconds"] == 3600,
      "7.A nouveau compte -> time.first_free_remaining_seconds = 3600")
tok = fresh()                                        # Premium, gratuite consommée
check(_state(tok)["time"]["premium_remaining_seconds"] == 28800,
      "7.B Premium actif -> time.premium_remaining_seconds = 28800")
reset_db(); seed_account(UID1, first_consultation_used_at=_FF_USED,
                         purchased_seconds_remaining=4200)
tok = A.create_app_session(UID1)
jt = _state(tok)["time"]
check(jt["purchased_remaining_seconds"] == 4200, "7.C purchased persisté exposé (4200)")
check(jt["total_remaining_seconds"]
      == jt["first_free_remaining_seconds"] + jt["premium_remaining_seconds"]
      + jt["purchased_remaining_seconds"],
      "7.D total = somme exacte des 3 buckets")

# --- 7.E/F — window_active / window_expires_at ---------------------------------
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY):
    _post(tok, "démarre")
_cid7 = FAKE["consultations"][0]["id"]
# TIMER-A.3c-2c : le POST touche désormais la fenêtre -> elle est ACTIVE juste
# après (last_activity_at = now), plus NULL comme au temps du câblage A.3a.
check(_state(tok)["time"]["window_active"] is True
      and isinstance(_state(tok)["time"]["window_expires_at"], str),
      "7.E juste après un POST -> fenêtre active, window_expires_at posé")
_la = A._utcnow()
_set_time_meter(_cid7, _la, _la)
jt = _state(tok)["time"]
_expected = A._ts_iso(_la + timedelta(seconds=300))
check(jt["window_active"] is True and jt["window_expires_at"] == _expected,
      "7.F fenêtre active -> window_active true, window_expires_at = last_activity + 5 min")

# --- 7.G/H/I — settle progressif via /state ----------------------------------
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY):
    _post(tok, "go")
_cid7 = FAKE["consultations"][0]["id"]
_t0 = A._utcnow()
_set_time_meter(_cid7, _t0, _t0)
with patch.object(A, "_utcnow", return_value=_t0 + timedelta(seconds=180)):
    _state(tok)
check(FAKE["accounts"][0]["first_free_seconds_remaining"] == 0
      and FAKE["consultation_allowance"][0]["monthly_used_seconds"] == 180,
      "7.G state à +3 min -> 180 s Premium consommées (first_free déjà à 0)")
with patch.object(A, "_utcnow", return_value=_t0 + timedelta(seconds=180)):
    _state(tok)
check(FAKE["consultation_allowance"][0]["monthly_used_seconds"] == 180,
      "7.H même state répété au même instant -> aucun double débit")
with patch.object(A, "_utcnow", return_value=_t0 + timedelta(seconds=480)):
    _state(tok)
check(FAKE["consultation_allowance"][0]["monthly_used_seconds"] == 300,
      "7.I state à +8 min -> fenêtre plafonnée à 300 s au total")

# --- 7.J — seconds_remaining == total_remaining_seconds ----------------------
js = _state(tok)
check(js["consultation"]["seconds_remaining"] == js["time"]["total_remaining_seconds"],
      "7.J consultation.seconds_remaining == time.total_remaining_seconds")

# --- 7.PRIO — AUDIT CIBLÉ A.3c-2c : priorité stricte des buckets au settle ---
# Un débit de N s avec first_free suffisant NE TOUCHE QUE first_free : Premium
# et purchased restent INCHANGÉS. first_consultation_used_at posé au 1er DÉBIT
# RÉEL (ici pendant le GET /state à +180 s, jamais au simple POST/touch).
reset_db()
seed_account(UID1, first_consultation_used_at=None,          # gratuite JAMAIS consommée
             first_free_seconds_remaining=3600,
             purchased_seconds_remaining=7200)
A.provision_allowance(UID1, A._utcnow() - timedelta(days=1),
                      A._utcnow() + timedelta(days=29), monthly_limit=4)
# monthly_allowance_seconds = 28800 / monthly_used_seconds = 0 (DEFAULT v34)
_TP = A._utcnow()
tok = A.create_app_session(UID1)
with patch.object(A, "_utcnow", return_value=_TP), \
     patch.object(A, "call_llm", return_value=REPLY):
    _post(tok, "go")
_acc = FAKE["accounts"][0]
_alw = FAKE["consultation_allowance"][0]
_con = FAKE["consultations"][0]
check(_acc["first_free_seconds_remaining"] == 3600
      and _alw["monthly_allowance_seconds"] == 28800
      and _alw["monthly_used_seconds"] == 0
      and _acc["purchased_seconds_remaining"] == 7200
      and _acc["first_consultation_used_at"] is None,
      "7.PRIO-a juste après POST -> AUCUN débit (buckets intacts, fcua NULL)")
check(_con["last_activity_at"] == _TP and _con["billed_until"] == _TP,
      "7.PRIO-b consultation ouverte : last_activity_at = billed_until = T0")
with patch.object(A, "_utcnow", return_value=_TP + timedelta(seconds=180)):
    _js = _state(tok)
check(_acc["first_free_seconds_remaining"] == 3420,
      "7.PRIO-c GET /state +180 s -> first_free 3600 -> 3420")
check(_alw["monthly_used_seconds"] == 0,
      "7.PRIO-d Premium INCHANGÉ (monthly_used_seconds reste 0)")
check(_acc["purchased_seconds_remaining"] == 7200,
      "7.PRIO-e purchased INCHANGÉ (7200)")
check(_acc["first_consultation_used_at"] == _TP + timedelta(seconds=180),
      "7.PRIO-f first_consultation_used_at posé à l'instant du 1er débit réel (T0+180 s)")
check(_js["time"]["first_free_remaining_seconds"] == 3420
      and _js["time"]["premium_remaining_seconds"] == 28800
      and _js["time"]["purchased_remaining_seconds"] == 7200
      and _js["time"]["total_remaining_seconds"] == 39420,
      "7.PRIO-g time exposé = 3420 + 28800 + 7200 = 39420")

# --- 7.PRIO2 — frontière first_free -> Premium sur un settle unique ----------
reset_db()
seed_account(UID1, first_consultation_used_at=None,
             first_free_seconds_remaining=100, purchased_seconds_remaining=500)
A.provision_allowance(UID1, A._utcnow() - timedelta(days=1),
                      A._utcnow() + timedelta(days=29), monthly_limit=4)
FAKE["consultation_allowance"][0]["monthly_allowance_seconds"] = 1000
FAKE["consultation_allowance"][0]["monthly_used_seconds"] = 0
_TP = A._utcnow()
tok = A.create_app_session(UID1)
with patch.object(A, "_utcnow", return_value=_TP), \
     patch.object(A, "call_llm", return_value=REPLY):
    _post(tok, "go")
_acc = FAKE["accounts"][0]
_alw = FAKE["consultation_allowance"][0]
with patch.object(A, "_utcnow", return_value=_TP + timedelta(seconds=180)):
    _state(tok)
check(_acc["first_free_seconds_remaining"] == 0
      and _alw["monthly_used_seconds"] == 80
      and _acc["purchased_seconds_remaining"] == 500
      and _acc["first_consultation_used_at"] is not None,
      "7.PRIO2-a débit 180 s -> ff 100->0, premium 1000->920 (used 80), purchased 500 intact, fcua posé")
with patch.object(A, "_utcnow", return_value=_TP + timedelta(seconds=230)):
    _state(tok)
check(_acc["first_free_seconds_remaining"] == 0
      and _alw["monthly_used_seconds"] == 130
      and _acc["purchased_seconds_remaining"] == 500,
      "7.PRIO2-b nouveau débit 50 s -> premium used 80->130 (920->870), ff 0, purchased 500")

# --- 7.PRIO3 — frontière Premium -> purchased, aucun négatif ----------------
reset_db()
seed_account(UID1, first_consultation_used_at=_FF_USED,
             first_free_seconds_remaining=0, purchased_seconds_remaining=500)
A.provision_allowance(UID1, A._utcnow() - timedelta(days=1),
                      A._utcnow() + timedelta(days=29), monthly_limit=4)
FAKE["consultation_allowance"][0]["monthly_allowance_seconds"] = 28800
FAKE["consultation_allowance"][0]["monthly_used_seconds"] = 28700   # premium restant = 100
_TP = A._utcnow()
tok = A.create_app_session(UID1)
with patch.object(A, "_utcnow", return_value=_TP), \
     patch.object(A, "call_llm", return_value=REPLY):
    _post(tok, "go")
_acc = FAKE["accounts"][0]
_alw = FAKE["consultation_allowance"][0]
with patch.object(A, "_utcnow", return_value=_TP + timedelta(seconds=180)):
    _state(tok)
check(_alw["monthly_used_seconds"] == 28800
      and _acc["purchased_seconds_remaining"] == 420
      and _acc["first_free_seconds_remaining"] == 0
      and _acc["purchased_seconds_remaining"] >= 0,
      "7.PRIO3 débit 180 s -> premium 100->0 (used 28800), purchased 500->420, aucun négatif")

tok = fresh()   # rétablit l'état pour les scénarios suivants

# --- 7.K — shim quota exact -------------------------------------------------
tok = fresh()
FAKE["consultation_allowance"][0]["monthly_used_seconds"] = 5400   # 1 h 30 consommée
jq = _state(tok)["quota"]
check(jq["monthly_limit"] == 8
      and jq["monthly_remaining"] == 7          # ceil((28800-5400)/3600) = ceil(6.5) = 7
      and jq["monthly_used"] == 1,              # 8 - 7
      "7.K shim : monthly_limit 8, monthly_remaining ceil(premium/3600)=7, monthly_used 1")

# --- 7.L — first_free_available dérivé du temps -----------------------------
tok = fresh(premium=False, first_free=True)
check(_state(tok)["quota"]["first_free_available"] is True, "7.L compte neuf -> first_free_available true")
tok = fresh(premium=False)                                       # gratuite consommée
check(_state(tok)["quota"]["first_free_available"] is False, "7.L2 gratuite consommée -> false")

# --- 7.M — earned_available legacy préservé --------------------------------
tok = fresh(premium=False)
A.grant_earned_credit(UID1, "referral")
A.grant_earned_credit(UID1, "share_30d")
check(_state(tok)["quota"]["earned_available"] == 2
      and sum(1 for e in FAKE["earned_credits"] if e["consumed_at"] is None) == 2,
      "7.M earned_credits legacy exposés (2) et NON consommés par le GET")

# --- 7.N/O/P — endpoint sans LLM, ne crée rien, n'incrémente pas le legacy ---
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY) as m_llm7:
    _post(tok, "x")
_before = (len(FAKE["consultations"]), FAKE["consultation_allowance"][0]["monthly_used"])
_cid7 = FAKE["consultations"][0]["id"]
_t0 = A._utcnow()
_set_time_meter(_cid7, _t0, _t0)
with patch.object(A, "call_llm", return_value=REPLY) as m_llm7b, \
     patch.object(A, "_utcnow", return_value=_t0 + timedelta(seconds=120)):
    _state(tok)
check(m_llm7b.call_count == 0, "7.N GET /state n'appelle JAMAIS le LLM")
check(len(FAKE["consultations"]) == _before[0], "7.O GET /state ne crée AUCUNE consultation")
check(FAKE["consultation_allowance"][0]["monthly_used"] == _before[1],
      "7.P GET /state n'incrémente JAMAIS monthly_used legacy (seul monthly_used_seconds bouge)")

# --- 8. fenêtre expirée : REPRISE même conseiller / NOUVELLE si conseiller change
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY):
    r8a = _post(tok, "premier")
_cid8 = r8a.get_json()["consultation"]["id"]
_expire_window()
# 8a. > 5 min, MÊME conseiller préféré -> reprise de la MÊME consultation logique
with patch.object(A, "call_llm", return_value=REPLY):
    r8b = _post(tok, "deuxième, bien plus tard")
j8b = r8b.get_json()
check(j8b["consultation"]["id"] == _cid8 and j8b["consultation"]["opened_now"] is False,
      "8a fenêtre expirée + même conseiller -> reprise même consultation (opened_now false)")
check(FAKE["consultation_allowance"][0]["monthly_used"] == 0,
      "8b aucun débit d'unité legacy (monthly_used reste 0)")
check(len(FAKE["consultations"]) == 1, "8c toujours une seule consultation logique")
# 8d. > 5 min, conseiller préféré DIFFÉRENT -> NOUVELLE consultation, ancien advisor intact
_expire_window()
A.update_app_profile(UID1, guide="maia")
with patch.object(A, "call_llm", return_value=REPLY) as m_llm8d:
    r8d = _post(tok, "je veux changer de guide")
j8d = r8d.get_json()
check(j8d["consultation"]["id"] != _cid8 and j8d["consultation"]["opened_now"] is True
      and j8d["consultation"]["advisor_id"] == "maia",
      "8d fenêtre expirée + conseiller différent -> nouvelle consultation avec maia")
check(A.GUIDES["maia"]["nom"] in m_llm8d.call_args[0][0][0]["content"],
      "8e LLM appelé avec le NOUVEAU conseiller (maia)")
check(next(c for c in FAKE["consultations"] if c["id"] == _cid8)["advisor_id"] == "selena",
      "8f ancienne consultation : advisor_id selena JAMAIS modifié")
check(len(FAKE["consultations"]) == 2, "8g exactement 2 consultations logiques")

# --- 9. payload JSON exact / cohérent (contrat temps) --------------------------
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY):
    j9 = _post(tok).get_json()
check(set(j9.keys()) == {"reply", "consultation", "time", "quota"},
      "9a clés racine = reply / consultation / time / quota")
check(set(j9["consultation"].keys()) == {"id", "advisor_id", "started_at", "expires_at",
                                          "seconds_remaining", "credit_source", "opened_now"},
      "9b clés consultation exactes")
check(set(j9["time"].keys()) == {"first_free_remaining_seconds", "premium_remaining_seconds",
                                  "purchased_remaining_seconds", "total_remaining_seconds",
                                  "window_active", "window_expires_at"},
      "9c clés time exactes")
check(set(j9["quota"].keys()) == {"is_premium", "monthly_limit", "monthly_used",
                                   "monthly_remaining", "earned_available",
                                   "first_free_available",
                                   "period_start", "period_end"},
      "9d clés quota shim exactes (dont first_free_available)")
check(j9["consultation"]["credit_source"] == "time"
      and j9["consultation"]["seconds_remaining"] == j9["time"]["total_remaining_seconds"],
      "9e credit_source = time, seconds_remaining = time.total_remaining_seconds")
check(isinstance(j9["consultation"]["started_at"], str)
      and isinstance(j9["consultation"]["expires_at"], str),
      "9f started_at / expires_at sérialisés en ISO string (compat, jamais cutoff)")

# --- 10. messages.consultation_id : user + assistant sur la même session -----
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY):
    jc = _post(tok, "rattachement").get_json()
_cid10 = jc["consultation"]["id"]
_sess_msgs = [m for m in FAKE["messages"] if m["user_id"] == UID1]
check(len(_sess_msgs) == 2 and {m["role"] for m in _sess_msgs} == {"user", "assistant"},
      "10a un tour = 2 messages (user + assistant)")
check(all(m["consultation_id"] == _cid10 for m in _sess_msgs),
      "10b les deux portent le consultation_id de la session")

# --- 11. compte supprimé -> 401 (require_app_auth), aucune consultation ------
tok = fresh()
FAKE["accounts"][0]["deleted_at"] = A._utcnow()
with patch.object(A, "call_llm", return_value=REPLY) as m_llm11:
    r11 = _post(tok)
check(r11.status_code == 401 and m_llm11.call_count == 0
      and len(FAKE["consultations"]) == 0,
      "11 compte supprimé -> 401, aucun LLM, aucune consultation")

# --- 12. validation message inchangée (avant le moteur) ---------------------
tok = fresh()
r12a = client.post("/api/consultation/message", json={"message": "   "},
                   headers={"Authorization": f"Bearer {tok}"})
r12b = client.post("/api/consultation/message", json={"message": "x" * 4001},
                   headers={"Authorization": f"Bearer {tok}"})
check(r12a.status_code == 400 and r12b.status_code == 400
      and len(FAKE["consultations"]) == 0,
      "12 message vide / trop long -> 400 AVANT toute ouverture de consultation")

# --- 13. routes enregistrées ----------------------------------------------
_rules = {r.rule for r in A.app.url_map.iter_rules()}
check({"/api/consultation/message", "/api/consultation/state"}.issubset(_rules),
      "13a routes message + state enregistrées")
check("/api/consultation/start" not in _rules, "13b PAS de route /start")

# --- 14. get_reply legacy strictement inchangé ---------------------------
import inspect
check(str(inspect.signature(A.get_reply)) ==
      "(phone, user_message, depuis_pub=False, user_msg_pre_inserted=False)",
      "14 get_reply(phone, ...) : signature legacy inchangée")

# --- 15. GRATUITE (first_free) via les routes, contrat TEMPS --------------
# 15a. GET /state d'un compte neuf : first_free_available=true, aucune écriture
tok = fresh(premium=False, first_free=True)
for _ in range(3):
    js15 = client.get("/api/consultation/state",
                      headers={"Authorization": f"Bearer {tok}"}).get_json()
check(js15["quota"]["first_free_available"] is True
      and js15["consultation"] is None
      and len(FAKE["consultations"]) == 0,
      "15a GET /state x3 (compte neuf) -> first_free_available true, aucune consultation")
check(FAKE["accounts"][0]["first_consultation_used_at"] is None,
      "15a2 GET /state n'écrit rien : first_consultation_used_at toujours NULL")

# 15b. 1er POST /message -> 200 ; credit_source=time ; le touch NE débite RIEN
# donc first_consultation_used_at reste NULL (posé seulement au 1er débit réel).
with patch.object(A, "call_llm", return_value=REPLY) as m_llm15:
    r15 = _post(tok)
j15 = r15.get_json()
check(r15.status_code == 200 and m_llm15.call_count == 1
      and j15["consultation"]["credit_source"] == "time"
      and j15["consultation"]["opened_now"] is True,
      "15b 1er message -> 200, credit_source = time")
check(j15["time"]["first_free_remaining_seconds"] == 3600
      and j15["time"]["total_remaining_seconds"] == 3600
      and j15["quota"]["first_free_available"] is True
      and FAKE["consultation_allowance"] == [],
      "15b2 1er touch ne débite rien : first_free 3600 intact, first_free_available true")
check(FAKE["accounts"][0]["first_consultation_used_at"] is None,
      "15b3 first_consultation_used_at TOUJOURS NULL après le seul touch (pas de débit)")
_cid15 = j15["consultation"]["id"]
_JSON_FIRST_FREE = dict(j15)     # §28 A — succès first_free

# 15c. reprise fenêtre active -> même id, opened_now false
with patch.object(A, "call_llm", return_value=REPLY):
    j15c = _post(tok, "suite").get_json()
check(j15c["consultation"]["id"] == _cid15
      and j15c["consultation"]["opened_now"] is False
      and len(FAKE["consultations"]) == 1,
      "15c 2e message < 5 min -> même consultation, opened_now false")

# 15R. §26.R — first_consultation_used_at posé au PREMIER DÉBIT RÉEL de first_free
_t15 = A._utcnow()
_set_time_meter(_cid15, _t15, _t15)
with patch.object(A, "call_llm", return_value=REPLY), \
     patch.object(A, "_utcnow", return_value=_t15 + timedelta(seconds=180)):
    j15r = _post(tok, "180 s plus tard").get_json()
check(FAKE["accounts"][0]["first_free_seconds_remaining"] == 3420
      and FAKE["accounts"][0]["first_consultation_used_at"] is not None,
      "15R settle 180 s de first_free -> reste 3420, first_consultation_used_at posé")
check(j15r["consultation"]["credit_source"] == "time"
      and FAKE["consultation_allowance"] == [],
      "15R2 credit_source reste time, aucun débit Premium (compte sans allowance)")

# 15d / §26.H — exhaustion : plus aucun temps -> 402 time_exhausted
_drain_first_free()
_expire_window()
with patch.object(A, "call_llm", return_value=REPLY) as m_llm15d:
    r15d = _post(tok)
check(r15d.status_code == 402 and r15d.get_json()["error"] == "time_exhausted"
      and m_llm15d.call_count == 0
      and r15d.get_json()["quota"]["first_free_available"] is False,
      "15d first_free épuisée + aucun autre droit -> 402 time_exhausted")

# 15e. Premium + gratuite : le 1er POST expose les DEUX buckets, monthly_used legacy 0
tok = fresh(premium=True, first_free=True)
with patch.object(A, "call_llm", return_value=REPLY):
    j15e = _post(tok).get_json()
check(j15e["consultation"]["credit_source"] == "time"
      and j15e["time"]["first_free_remaining_seconds"] == 3600
      and j15e["time"]["premium_remaining_seconds"] == 28800
      and j15e["time"]["total_remaining_seconds"] == 32400,
      "15e Premium neuf + gratuite -> time = 3600 + 28800 = 32400, credit_source time")
check(j15e["quota"]["monthly_used"] == 0
      and FAKE["consultation_allowance"][0]["monthly_used"] == 0,
      "15e2 monthly_used legacy = 0 (aucune unité débitée)")

# --- 16. §26.I — waterfall first_free -> premium sur un même settle -----------
tok = fresh(premium=True, first_free=True)
with patch.object(A, "call_llm", return_value=REPLY):
    _cid16 = _post(tok).get_json()["consultation"]["id"]
FAKE["accounts"][0]["first_free_seconds_remaining"] = 10
_t16 = A._utcnow()
_set_time_meter(_cid16, _t16, _t16)
with patch.object(A, "call_llm", return_value=REPLY) as m_llm16, \
     patch.object(A, "_utcnow", return_value=_t16 + timedelta(seconds=30)):
    j16 = _post(tok, "30 s plus tard").get_json()
check(FAKE["accounts"][0]["first_free_seconds_remaining"] == 0
      and FAKE["consultation_allowance"][0]["monthly_used_seconds"] == 20,
      "16a settle 30 s -> 10 s first_free puis 20 s Premium")
check(FAKE["accounts"][0]["first_consultation_used_at"] is not None,
      "16b first_consultation_used_at posé (au moins 1 s de first_free débitée)")
check(FAKE["consultation_allowance"][0]["monthly_used"] == 0
      and m_llm16.call_count == 1
      and j16["consultation"]["credit_source"] == "time",
      "16c aucun monthly_used legacy, POST autorisé (total > 0), credit_source time")

# --- 17. §26.J — waterfall premium -> purchased, aucun bucket négatif --------
reset_db()
seed_account(UID1, first_consultation_used_at=_FF_USED,
             first_free_seconds_remaining=0, purchased_seconds_remaining=100)
A.provision_allowance(UID1, A._utcnow() - timedelta(days=1),
                      A._utcnow() + timedelta(days=29), monthly_limit=4)
FAKE["consultation_allowance"][0]["monthly_used_seconds"] = 28790     # premium restant = 10
tok = A.create_app_session(UID1)
with patch.object(A, "call_llm", return_value=REPLY):
    _cid17 = _post(tok).get_json()["consultation"]["id"]
_t17 = A._utcnow()
_set_time_meter(_cid17, _t17, _t17)
with patch.object(A, "call_llm", return_value=REPLY), \
     patch.object(A, "_utcnow", return_value=_t17 + timedelta(seconds=30)):
    j17 = _post(tok, "30 s plus tard").get_json()
check(FAKE["consultation_allowance"][0]["monthly_used_seconds"] == 28800
      and FAKE["accounts"][0]["purchased_seconds_remaining"] == 80,
      "17a settle 30 s -> 10 s Premium puis 20 s purchased (premium 0, purchased 80)")
check(FAKE["accounts"][0]["purchased_seconds_remaining"] >= 0
      and FAKE["consultation_allowance"][0]["monthly_used_seconds"]
      <= FAKE["consultation_allowance"][0]["monthly_allowance_seconds"],
      "17b aucun bucket négatif, monthly_used_seconds borné par l'allocation")
check(j17["consultation"]["credit_source"] == "time"
      and j17["time"]["purchased_remaining_seconds"] == 80,
      "17c POST autorisé, time.purchased_remaining_seconds = 80")
_JSON_PURCHASED = dict(j17)      # §28 C — succès avec purchased

# --- 18. §26.S — POST séquentiels : aucune duplication de consultation -------
tok = fresh()
for _i in range(5):
    with patch.object(A, "call_llm", return_value=REPLY):
        _post(tok, f"message {_i}")
check(len(FAKE["consultations"]) == 1,
      "18 5 POST rapprochés -> UNE seule consultation logique")

# --- 19. §26.V — échec LLM : le helper a déjà commit, fenêtre reste ouverte ---
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY):
    _post(tok, "ouverture")
_cid19 = FAKE["consultations"][0]["id"]
_n_msgs_before = len(FAKE["messages"])
_expire_window()
try:
    with patch.object(A, "call_llm", side_effect=RuntimeError("llm indisponible")):
        r19 = _post(tok, "et là le LLM tombe")
    _llm_fail_behaviour = f"HTTP {r19.status_code}"
except RuntimeError:
    _llm_fail_behaviour = "exception propagée (pas de compensation dans ce lot)"
_c19 = next(c for c in FAKE["consultations"] if c["id"] == _cid19)
check(len(FAKE["consultations"]) == 1 and _c19["last_activity_at"] is not None,
      "19a échec LLM : consultation/touch du helper déjà commit (fenêtre ouverte)")
check(sum(1 for m in FAKE["messages"] if m["role"] == "assistant"
          and m["content"] == "llm indisponible") == 0,
      "19b aucun message assistant fantôme persisté sur échec LLM")

# --- 20. §27 — preuve structurelle (AST) : plus d'appel open_or_get_consultation
import ast as _ast
_src_pm = inspect.getsource(A.api_consultation_message)
_tree_pm = _ast.parse(_src_pm)
_calls_pm = {n.func.id for n in _ast.walk(_tree_pm)
             if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name)}
_calls_pm |= {n.func.attr for n in _ast.walk(_tree_pm)
              if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)}
check("_open_time_consultation_flow_tx" in _calls_pm,
      "20a AST : api_consultation_message APPELLE _open_time_consultation_flow_tx")
check("open_or_get_consultation" not in _calls_pm,
      "20b AST : api_consultation_message N'APPELLE PLUS open_or_get_consultation")

# --- §28 : JSON RÉELS capturés en exécution --------------------------------
print("-" * 64)
print("§28 JSON RÉELS CAPTURÉS :")
import json as _json
for _lbl, _obj in (("A succès first_free", _JSON_FIRST_FREE),
                   ("B succès Premium", _JSON_PREMIUM),
                   ("C succès purchased", _JSON_PURCHASED),
                   ("D 402 time_exhausted", _JSON_402)):
    print(f"  [{_lbl}] {_json.dumps(_obj, default=str, sort_keys=True)}")
check(all(x is not None for x in (_JSON_FIRST_FREE, _JSON_PREMIUM, _JSON_PURCHASED, _JSON_402)),
      "§28 les 4 JSON réels ont été capturés")

# === GET /api/consultation/messages — historique de la consultation ACTIVE ===
# (l'app "Reprendre ma consultation" doit réafficher la conversation en cours)

def _get_msgs(tok, qs=""):
    return client.get("/api/consultation/messages" + qs,
                      headers={"Authorization": f"Bearer {tok}"})


# --- M-A. auth Bearer obligatoire ------------------------------------------
r_noauth = client.get("/api/consultation/messages")
r_badauth = client.get("/api/consultation/messages",
                       headers={"Authorization": "Bearer pas-un-vrai-jeton"})
check(r_noauth.status_code == 401, "M-A1 sans Bearer -> 401")
check(r_badauth.status_code == 401, "M-A2 Bearer invalide -> 401")

# --- M-B. aucune consultation active -> { consultation_id: null, messages: [] }
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY) as m_llm_b:
    rb = _get_msgs(tok)
jb = rb.get_json()
check(rb.status_code == 200, "M-B1 aucune session active -> 200")
check(jb == {"consultation_id": None, "messages": []},
      "M-B2 payload = { consultation_id: null, messages: [] }")
check(m_llm_b.call_count == 0 and len(FAKE["consultations"]) == 0,
      "M-B3 aucun LLM, aucune consultation ouverte par le GET")
check("no-store" in rb.headers.get("Cache-Control", ""), "M-B4 Cache-Control: no-store")

# --- M-C. consultation active SANS message -> [] --------------------------
tok = fresh()
_now = A._utcnow()
FAKE["consultations"].append({
    "id": "c-vide", "user_id": UID1, "advisor_id": "selena",
    "started_at": _now, "expires_at": _now + timedelta(hours=2),
    "credit_source": "monthly", "created_at": _now,
})
jc = _get_msgs(tok).get_json()
check(jc["consultation_id"] == "c-vide" and jc["messages"] == [],
      "M-C consultation active vide -> messages []")

# --- M-D. consultation active avec plusieurs messages -> ordre chronologique
tok = fresh()
with patch.object(A, "call_llm", return_value="reponse-1"):
    _post(tok, "question-1")
with patch.object(A, "call_llm", return_value="reponse-2"):
    _post(tok, "question-2")
jd = _get_msgs(tok).get_json()
check([m["content"] for m in jd["messages"]] == ["question-1", "reponse-1",
                                                 "question-2", "reponse-2"],
      "M-D messages rendus dans l'ordre chronologique (timestamp/id ASC)")
check(len(jd["messages"]) == 4, "M-D2 exactement les 4 messages de la session")

# --- M-E / M-F. seule la consultation ACTIVE compte ; l'ancienne est exclue
# Modèle TEMPS : nouvelle consultation logique = fenêtre expirée + conseiller
# préféré différent (get_or_open_time_consultation_tx). GET /messages reste
# legacy (§25) et prend la plus récente par started_at.
tok = fresh()
with patch.object(A, "call_llm", return_value="vieille-reponse"):
    r_old = _post(tok, "vieux-message")
old_cid = r_old.get_json()["consultation"]["id"]
_expire_window()
A.update_app_profile(UID1, guide="maia")
with patch.object(A, "call_llm", return_value="nouvelle-reponse"):
    r_new = _post(tok, "nouveau-message")
new_cid = r_new.get_json()["consultation"]["id"]
jef = _get_msgs(tok).get_json()
check(new_cid != old_cid, "M-E1 nouvelle consultation != ancienne")
check(jef["consultation_id"] == new_cid, "M-E2 renvoie l'id de la consultation active")
check([m["content"] for m in jef["messages"]] == ["nouveau-message", "nouvelle-reponse"],
      "M-F messages de l'ancienne consultation exclus (seule l'active)")

# --- M-G. messages d'un AUTRE utilisateur jamais renvoyés ----------------
tok1 = fresh(UID1)
seed_account(UID2, first_consultation_used_at=_FF_USED)
_seed_premium(UID2)
tok2 = A.create_app_session(UID2)
with patch.object(A, "call_llm", return_value="reponse-pour-user1"):
    _post(tok1, "message-prive-user1")
with patch.object(A, "call_llm", return_value="reponse-pour-user2"):
    client.post("/api/consultation/message", json={"message": "message-user2"},
                headers={"Authorization": f"Bearer {tok2}"})
jg = _get_msgs(tok2).get_json()
check([m["content"] for m in jg["messages"]] == ["message-user2", "reponse-pour-user2"],
      "M-G1 user2 ne reçoit que ses propres messages")
check(all("user1" not in m["content"] for m in jg["messages"]),
      "M-G2 aucun contenu de user1 dans la réponse de user2")

# --- M-H. rôles : seuls user / assistant sont exposés --------------------
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY):
    jc_h = _post(tok, "coucou").get_json()
cid_h = jc_h["consultation"]["id"]
FAKE["messages"].append({"id": 999999, "user_id": UID1, "phone": None,
                         "role": "system", "content": "PROMPT-INTERNE-CONFIDENTIEL",
                         "timestamp": A._utcnow().isoformat(), "consultation_id": cid_h})
jh = _get_msgs(tok).get_json()
check([m["role"] for m in jh["messages"]] == ["user", "assistant"],
      "M-H1 rôles sérialisés = user puis assistant")
check(all(m["role"] in ("user", "assistant") for m in jh["messages"]),
      "M-H2 rôle 'system' non exposé à l'app")
check(all("CONFIDENTIEL" not in m["content"] for m in jh["messages"]),
      "M-H3 contenu d'un message non-conversationnel non exposé")
check(all(set(m.keys()) == {"role", "content", "timestamp"} for m in jh["messages"]),
      "M-H4 clés de chaque message = role / content / timestamp")

# --- M-I. timestamps présents ------------------------------------------
check(all(isinstance(m["timestamp"], str) and m["timestamp"] for m in jh["messages"]),
      "M-I timestamp présent (chaîne non vide) sur chaque message")

# --- M-J. le GET ne consomme aucun quota, n'ouvre aucune consultation ----
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY):
    _post(tok, "on démarre")
_snap_j = (len(FAKE["consultations"]),
           FAKE["consultation_allowance"][0]["monthly_used"],
           len(FAKE["messages"]),
           sum(1 for e in FAKE["earned_credits"] if e["consumed_at"] is None))
with patch.object(A, "call_llm", return_value=REPLY) as m_llm_j2:
    for _ in range(3):
        _get_msgs(tok)
check(m_llm_j2.call_count == 0, "M-J1 3 GET consécutifs : aucun appel LLM")
check((len(FAKE["consultations"]),
       FAKE["consultation_allowance"][0]["monthly_used"],
       len(FAKE["messages"]),
       sum(1 for e in FAKE["earned_credits"] if e["consumed_at"] is None)) == _snap_j,
      "M-J2 consultations / quota / messages / earned inchangés après 3 GET")

# --- M-N. TIMER-A.3d : expires_at dépassé (legacy 2h) n'exclut plus l'historique
tok = fresh()
_T0n = A._utcnow()
with patch.object(A, "_utcnow", return_value=_T0n), \
     patch.object(A, "call_llm", return_value="reponse-vieille"):
    _post(tok, "message-vieux")
_cid_n = FAKE["consultations"][0]["id"]
# expires_at = T0 + 2h (valeur compat) largement dépassée, 24h plus tard
with patch.object(A, "_utcnow", return_value=_T0n + timedelta(hours=24)):
    jn = _get_msgs(tok).get_json()
check(jn["consultation_id"] == _cid_n
      and [m["content"] for m in jn["messages"]] == ["message-vieux", "reponse-vieille"],
      "M-N expires_at dépassé (24h) -> l'historique reste visible (plus de cutoff expires_at)")

# --- M-O. TIMER-A.3d : fenêtre d'activité 300 s inactive depuis 1h -----------
tok = fresh()
with patch.object(A, "call_llm", return_value="reponse-inactive"):
    _post(tok, "message-inactif")
_cid_o = FAKE["consultations"][0]["id"]
_expire_window()          # last_activity_at = billed_until = now - 1h
_snap_acc_o = dict(FAKE["accounts"][0])
_snap_alw_o = dict(FAKE["consultation_allowance"][0])
_snap_con_o = dict(FAKE["consultations"][0])
jo = _get_msgs(tok).get_json()
check(jo["consultation_id"] == _cid_o
      and [m["content"] for m in jo["messages"]] == ["message-inactif", "reponse-inactive"],
      "M-O fenêtre inactive (1h) -> consultation + messages toujours renvoyés")
check(FAKE["accounts"][0] == _snap_acc_o
      and FAKE["consultation_allowance"][0] == _snap_alw_o
      and FAKE["consultations"][0] == _snap_con_o,
      "M-O2 aucune mutation : accounts / consultation_allowance / consultations inchangés "
      "(ni settle, ni touch, ni débit first_free/premium/purchased)")

# --- M-P. même conseiller repris après fenêtre expirée -> historique COMPLET -
tok = fresh()
_TP0 = A._utcnow()
with patch.object(A, "_utcnow", return_value=_TP0), \
     patch.object(A, "call_llm", return_value="reponse-1-p"):
    _post(tok, "message-1-p")
_cid_p = FAKE["consultations"][0]["id"]
_expire_window()
with patch.object(A, "_utcnow", return_value=_TP0 + timedelta(hours=6)), \
     patch.object(A, "call_llm", return_value="reponse-2-p"):
    _post(tok, "message-2-p")     # même advisor (selena) -> reprend la MÊME consultation
check(len(FAKE["consultations"]) == 1 and FAKE["consultations"][0]["id"] == _cid_p,
      "M-P0 même conseiller après fenêtre expirée -> même consultation logique reprise")
jp = _get_msgs(tok).get_json()
check(jp["consultation_id"] == _cid_p
      and [m["content"] for m in jp["messages"]] == ["message-1-p", "reponse-1-p",
                                                      "message-2-p", "reponse-2-p"],
      "M-P historique COMPLET (pas seulement les messages de la dernière fenêtre de 5 min)")

# --- M-Q. lecture seule ABSOLUE : preuve AST/code-only -----------------------
def _code_only(fn):
    """Source de `fn` sans son docstring — pour ne jamais confondre un mot
    mentionné en PROSE (docstring/commentaire) avec du code exécuté."""
    tree = _ast.parse(inspect.getsource(fn))
    fn_node = tree.body[0]
    if (fn_node.body and isinstance(fn_node.body[0], _ast.Expr)
            and isinstance(fn_node.body[0].value, _ast.Constant)
            and isinstance(fn_node.body[0].value.value, str)):
        fn_node.body = fn_node.body[1:]
    return _ast.unparse(fn_node)


_FORBIDDEN_M = ("_settle_consultation_time_tx", "_debit_consultation_seconds_tx",
                "_touch_consultation_activity_tx", "_open_time_consultation_flow_tx",
                "get_or_open_time_consultation_tx", "open_or_get_consultation",
                "attach_tirage_to_consultation", "get_reply_for_user_id")
_src_latest_full = inspect.getsource(A._latest_consultation_id_for_user_id)
_src_route_m_full = inspect.getsource(A.api_consultation_messages)
_code_latest = _code_only(A._latest_consultation_id_for_user_id)
_code_route_m = _code_only(A.api_consultation_messages)
_tree_m = _ast.parse(_src_latest_full + "\n" + _src_route_m_full)
_calls_m = {n.func.id for n in _ast.walk(_tree_m)
            if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name)}
_calls_m |= {n.func.attr for n in _ast.walk(_tree_m)
             if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)}
check(not (_calls_m & set(_FORBIDDEN_M)),
      "M-Q AST : GET /messages n'appelle AUCUNE primitive de mutation/settle/LLM du moteur temps")
check("expires_at" not in _code_latest,
      "M-Q2 code-only : _latest_consultation_id_for_user_id ne référence PAS expires_at "
      "(seule la docstring en parle, en PROSE, pour expliquer ce qui n'est plus fait)")
check("FOR UPDATE" not in _code_latest and "FOR UPDATE" not in _code_route_m,
      "M-Q3 code-only : aucun FOR UPDATE dans le chemin GET /messages")

# --- M-R. appels répétés : idempotence stricte y compris sur les buckets ----
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY):
    _post(tok, "idempotence")
_before_r = (dict(FAKE["accounts"][0]), dict(FAKE["consultation_allowance"][0]),
             dict(FAKE["consultations"][0]))
_json_before_r = _get_msgs(tok).get_json()
for _ in range(3):
    _json_r = _get_msgs(tok).get_json()
check(_json_r == _json_before_r, "M-R1 JSON identique sur appels répétés")
check((FAKE["accounts"][0], FAKE["consultation_allowance"][0], FAKE["consultations"][0]) == _before_r,
      "M-R2 accounts / consultation_allowance / consultations strictement inchangés "
      "(first_free, monthly_used_seconds, purchased, first_consultation_used_at, "
      "last_activity_at, billed_until)")

# --- M-K. identité = Bearer uniquement : ?user_id= ignoré ---------------
tok1 = fresh(UID1)
seed_account(UID2, first_consultation_used_at=_FF_USED)
_seed_premium(UID2)
tok2 = A.create_app_session(UID2)
with patch.object(A, "call_llm", return_value="reponse-pour-user2"):
    client.post("/api/consultation/message", json={"message": "message-user2"},
                headers={"Authorization": f"Bearer {tok2}"})
# user1 n'a AUCUNE session active et tente de forcer user_id=UID2 en query
jk = _get_msgs(tok1, "?user_id=" + UID2).get_json()
check(jk == {"consultation_id": None, "messages": []},
      "M-K ?user_id= ignoré : user1 (sans session) reste vide malgré la query")

# --- M-L. route enregistrée -------------------------------------------
_rules_m = {r.rule for r in A.app.url_map.iter_rules()}
check("/api/consultation/messages" in _rules_m,
      "M-L route GET /api/consultation/messages enregistrée")

print("-" * 64)
_total = _STATE["pass"] + _STATE["fail"]
if _STATE["fail"] == 0:
    print(f"✅ {_STATE['pass']}/{_total} tests passés")
    sys.exit(0)
else:
    print(f"❌ {_STATE['fail']} test(s) en échec sur {_total}")
    sys.exit(1)
