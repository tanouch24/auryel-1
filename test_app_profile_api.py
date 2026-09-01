"""
test_app_profile_api.py — B4.3 : synchronisation du profil onboarding Flutter
vers le backend (GET / PATCH /api/app/profile).

100 % local : psycopg2 mocké, get_conn -> fausse DB en mémoire (tables app +
moteur consultations B4.1), call_llm mocké, aucun réseau. Fake DB alignée sur
test_app_consultation.py.
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
                 first_free_seconds_remaining=3600,
                 purchased_seconds_remaining=0):
    FAKE["accounts"].append({"user_id": user_id, "email": email,
                             "deleted_at": deleted_at,
                             "first_consultation_used_at": first_consultation_used_at,
                             "first_free_seconds_remaining": first_free_seconds_remaining,
                             "purchased_seconds_remaining": purchased_seconds_remaining})


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

        # ---- moteur consultations 2 h / crédits (B4.1) ----
        elif k == ("SELECT user_id, first_consultation_used_at FROM accounts "
                   "WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE"):
            (uid,) = p
            r = next((a for a in FAKE["accounts"]
                      if a["user_id"] == uid and a["deleted_at"] is None), None)
            self._result = (uid, r.get("first_consultation_used_at")) if r else None

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

        # ---- MOTEUR TEMPS (TIMER-A.3c-2c) : flux _open_time_consultation_flow_tx ----
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
                self._result = (rows[0]["id"], rows[0]["last_activity_at"])

        elif k == ("SELECT id, advisor_id, last_activity_at, billed_until FROM consultations "
                   "WHERE user_id=%s ORDER BY started_at DESC LIMIT 1"):
            (uid,) = p
            rows = sorted([c for c in FAKE["consultations"] if c["user_id"] == uid],
                          key=lambda c: c["started_at"], reverse=True)
            if rows:
                c0 = rows[0]
                self._result = (c0["id"], c0["advisor_id"],
                                c0["last_activity_at"], c0["billed_until"])

        elif k == ("SELECT user_id, last_activity_at, billed_until FROM consultations "
                   "WHERE id=%s"):
            (cid,) = p
            r = next((c for c in FAKE["consultations"] if c["id"] == str(cid)), None)
            if r is not None:
                self._result = (r["user_id"], r["last_activity_at"], r["billed_until"])

        elif k == ("SELECT started_at, expires_at, credit_source FROM consultations "
                   "WHERE id=%s"):
            (cid,) = p
            r = next((c for c in FAKE["consultations"] if c["id"] == str(cid)), None)
            if r is not None:
                self._result = (r["started_at"], r["expires_at"], r["credit_source"])

        elif k == ("UPDATE consultations SET last_activity_at=%s, billed_until=%s "
                   "WHERE id=%s"):
            la, bu, cid = p
            for c in FAKE["consultations"]:
                if c["id"] == str(cid):
                    c["last_activity_at"] = la
                    c["billed_until"] = bu
                    self.rowcount = 1

        elif k == "UPDATE consultations SET last_activity_at=%s WHERE id=%s":
            la, cid = p
            for c in FAKE["consultations"]:
                if c["id"] == str(cid):
                    c["last_activity_at"] = la
                    self.rowcount = 1

        elif k == "UPDATE consultations SET billed_until=%s WHERE id=%s":
            bu, cid = p
            for c in FAKE["consultations"]:
                if c["id"] == str(cid):
                    c["billed_until"] = bu
                    self.rowcount = 1

        elif k == ("SELECT first_free_seconds_remaining, purchased_seconds_remaining "
                   "FROM accounts WHERE user_id=%s FOR UPDATE"):
            (uid,) = p
            r = next((a for a in FAKE["accounts"] if a["user_id"] == str(uid)), None)
            if r is not None:
                self._result = (r.get("first_free_seconds_remaining", 0),
                                r.get("purchased_seconds_remaining", 0))

        elif k == ("SELECT first_free_seconds_remaining, purchased_seconds_remaining "
                   "FROM accounts WHERE user_id=%s"):
            (uid,) = p
            r = next((a for a in FAKE["accounts"] if a["user_id"] == str(uid)), None)
            if r is not None:
                self._result = (r.get("first_free_seconds_remaining", 0),
                                r.get("purchased_seconds_remaining", 0))

        elif k == ("UPDATE accounts SET first_free_seconds_remaining=%s, "
                   "purchased_seconds_remaining=%s WHERE user_id=%s"):
            ff, pu, uid = p
            r = next((a for a in FAKE["accounts"] if a["user_id"] == str(uid)), None)
            if r is not None:
                r["first_free_seconds_remaining"] = ff
                r["purchased_seconds_remaining"] = pu
                self.rowcount = 1

        elif k == ("SELECT period_start, monthly_allowance_seconds, monthly_used_seconds "
                   "FROM consultation_allowance WHERE user_id=%s AND period_start <= %s "
                   "AND period_end > %s ORDER BY period_start DESC LIMIT 1 FOR UPDATE"):
            uid, now, _n2 = p
            rows = [a for a in FAKE["consultation_allowance"]
                    if a["user_id"] == uid and a["period_start"] <= now and a["period_end"] > now]
            rows.sort(key=lambda a: a["period_start"], reverse=True)
            if rows:
                a0 = rows[0]
                self._result = (a0["period_start"],
                                a0.get("monthly_allowance_seconds", 28800),
                                a0.get("monthly_used_seconds", 0))

        elif k == ("SELECT monthly_allowance_seconds, monthly_used_seconds "
                   "FROM consultation_allowance WHERE user_id=%s AND period_start <= %s "
                   "AND period_end > %s ORDER BY period_start DESC LIMIT 1"):
            uid, now, _n2 = p
            rows = [a for a in FAKE["consultation_allowance"]
                    if a["user_id"] == uid and a["period_start"] <= now and a["period_end"] > now]
            rows.sort(key=lambda a: a["period_start"], reverse=True)
            if rows:
                a0 = rows[0]
                self._result = (a0.get("monthly_allowance_seconds", 28800),
                                a0.get("monthly_used_seconds", 0))

        elif k == ("UPDATE consultation_allowance SET monthly_used_seconds=%s "
                   "WHERE user_id=%s AND period_start=%s"):
            mus, uid, ps = p
            r = next((a for a in FAKE["consultation_allowance"]
                      if a["user_id"] == uid and a["period_start"] == ps), None)
            if r is not None:
                r["monthly_used_seconds"] = mus
                self.rowcount = 1

        elif k == ("SELECT period_start, period_end FROM consultation_allowance "
                   "WHERE user_id=%s AND period_start <= %s AND period_end > %s "
                   "ORDER BY period_start DESC LIMIT 1"):
            uid, now, _n2 = p
            rows = [a for a in FAKE["consultation_allowance"]
                    if a["user_id"] == uid and a["period_start"] <= now and a["period_end"] > now]
            rows.sort(key=lambda a: a["period_start"], reverse=True)
            if rows:
                self._result = (rows[0]["period_start"], rows[0]["period_end"])

        else:
            raise AssertionError("SQL non géré par le fake B4.3 : " + k)


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


def fresh(uid=UID1, deleted_at=None):
    reset_db()
    seed_account(uid, deleted_at=deleted_at)
    return A.create_app_session(uid)


def _get(tok):
    return client.get("/api/app/profile", headers={"Authorization": f"Bearer {tok}"})


def _patch(tok, body):
    return client.patch("/api/app/profile", json=body,
                        headers={"Authorization": f"Bearer {tok}"})


def _profile_row(uid=UID1):
    return next((r for r in FAKE["app_profiles"] if r["user_id"] == uid), None)


print("=" * 64)
print("TEST APP PROFILE API — B4.3  (GET / PATCH /api/app/profile)")
print("=" * 64)

# --- 1. GET : auth requise --------------------------------------------------
tok = fresh()
r = client.get("/api/app/profile")
check(r.status_code == 401, "1a GET sans Bearer -> 401")
r = client.get("/api/app/profile", headers={"Authorization": "Bearer nope"})
check(r.status_code == 401, "1b GET Bearer invalide -> 401")

# --- 2. GET : profil créé à la volée, forme complète -----------------------
tok = fresh()
r = _get(tok)
j = r.get_json()
check(r.status_code == 200, "2a GET authentifié -> 200")
check(set(j.keys()) == {"user_id", "guide", "prenom", "date_naissance",
                        "chemin_de_vie", "signe_zodiaque"},
      "2b clés exactes")
check(j["user_id"] == UID1 and j["guide"] == "selena" and j["prenom"] == ""
      and j["date_naissance"] is None and j["chemin_de_vie"] == ""
      and j["signe_zodiaque"] == "",
      "2c profil neuf : guide=selena par défaut, reste vide")
check("no-store" in r.headers.get("Cache-Control", ""), "2d Cache-Control: no-store")

# --- 3. PATCH guide -------------------------------------------------------
tok = fresh()
_get(tok)  # crée le profil
r = _patch(tok, {"guide": "maia"})
j = r.get_json()
check(r.status_code == 200 and j["guide"] == "maia", "3a PATCH guide=maia -> 200, guide=maia")
check(_profile_row()["guide"] == "maia", "3b app_profiles.guide persisté = maia")
check(j["prenom"] == "" and j["date_naissance"] is None,
      "3c les autres champs restent inchangés")

# --- 4. PATCH prénom (trim) --------------------------------------------
r = _patch(tok, {"prenom": "  Nathanyel  "})
check(r.status_code == 200 and r.get_json()["prenom"] == "Nathanyel",
      "4a PATCH prénom -> trimé")
check(_profile_row()["prenom"] == "Nathanyel", "4b prénom persisté")
check(_profile_row()["guide"] == "maia", "4c guide NON réinitialisé par un PATCH prénom")

# --- 5. PATCH date_naissance + recalcul métier ------------------------
r = _patch(tok, {"date_naissance": "1984-01-01"})
j = r.get_json()
check(r.status_code == 200 and j["date_naissance"] == "1984-01-01", "5a PATCH date -> 200")
check(j["chemin_de_vie"] == str(A.calcul_chemin_de_vie("1984-01-01")),
      "5b chemin_de_vie recalculé via calcul_chemin_de_vie()")
check(j["signe_zodiaque"] == A.calcul_signe("1984-01-01"),
      "5c signe_zodiaque recalculé via calcul_signe()")
check(_profile_row()["chemin_de_vie"] == str(A.calcul_chemin_de_vie("1984-01-01"))
      and _profile_row()["signe_zodiaque"] == A.calcul_signe("1984-01-01"),
      "5d recalculs persistés")

# --- 6. PATCH partiel : body vide = idempotent, rien ne bouge ---------
before = dict(_profile_row())
r = _patch(tok, {})
check(r.status_code == 200, "6a PATCH {} -> 200")
after = dict(_profile_row())
check(after["guide"] == before["guide"] and after["prenom"] == before["prenom"]
      and after["date_naissance"] == before["date_naissance"],
      "6b PATCH {} : profil strictement inchangé (idempotent)")

# --- 7. guide invalide -> 400, rien modifié --------------------------
tok = fresh(); _get(tok)
_patch(tok, {"guide": "luna"})
r = _patch(tok, {"guide": "bob"})
check(r.status_code == 400 and r.get_json()["error"] == "invalid_guide",
      "7a guide inconnu -> 400 invalid_guide")
check(_profile_row()["guide"] == "luna", "7b guide inchangé après le 400")
r = _patch(tok, {"guide": 123})
check(r.status_code == 400, "7c guide non-string -> 400")

# --- 8. prénom vide / trop long -> 400 ------------------------------
r = _patch(tok, {"prenom": "   "})
check(r.status_code == 400 and r.get_json()["error"] == "invalid_prenom",
      "8a prénom vide -> 400 invalid_prenom")
r = _patch(tok, {"prenom": "x" * 41})
check(r.status_code == 400, "8b prénom > 40 caractères -> 400")

# --- 9. date invalide / future -> 400 -----------------------------
for bad in ["1984/01/01", "01-01-1984", "1984-13-01", "1984-02-30", "not-a-date", "1984-1-1"]:
    rr = _patch(tok, {"date_naissance": bad})
    check(rr.status_code == 400 and rr.get_json()["error"] == "invalid_date_naissance",
          f"9 date invalide {bad!r} -> 400")
r = _patch(tok, {"date_naissance": "2999-01-01"})
check(r.status_code == 400 and r.get_json()["error"] == "invalid_date_naissance",
      "9 date future -> 400")

# --- 10. user_id du body IGNORÉ (identité = Bearer) ---------------
tok1 = fresh(UID1); _get(tok1)
seed_account(UID2)
tok2 = A.create_app_session(UID2)
_get(tok2)
r = _patch(tok1, {"user_id": UID2, "guide": "orion"})
check(r.status_code == 200 and r.get_json()["user_id"] == UID1,
      "10a user_id du body ignoré : réponse porte le user_id de la session")
check(_profile_row(UID1)["guide"] == "orion" and _profile_row(UID2)["guide"] == "selena",
      "10b seul le profil de la session est modifié, l'autre intact")

# --- 11. compte supprimé / auth invalide -----------------------
tok = fresh(deleted_at=A._utcnow())
r = _get(tok)
check(r.status_code == 401, "11a GET, compte soft-supprimé -> 401 (auth existante)")
r = _patch(tok, {"guide": "maia"})
check(r.status_code == 401, "11b PATCH, compte soft-supprimé -> 401")

# --- 12. NON-RÉGRESSION : consultation active non impactée par PATCH guide ---
reset_db()
seed_account(UID1)
now = A._utcnow()
A.provision_allowance(UID1, now - timedelta(days=1), now + timedelta(days=29), monthly_limit=4)
tok = A.create_app_session(UID1)
_get(tok)  # profil créé, guide=selena

with patch.object(A, "call_llm", return_value=REPLY) as m1, \
     patch("auryel_bot.random.random", return_value=1.0):
    r_open = client.post("/api/consultation/message", json={"message": "bonjour selena"},
                         headers={"Authorization": f"Bearer {tok}"})
_cid = r_open.get_json()["consultation"]["id"]
check(r_open.get_json()["consultation"]["advisor_id"] == "selena",
      "12a consultation ouverte avec selena")
_sys_open = m1.call_args[0][0][0]["content"]
check(A.GUIDES["selena"]["nom"] in _sys_open, "12b system prompt = persona Séléna")

# PATCH guide -> maia PENDANT la consultation active
rp = _patch(tok, {"guide": "maia"})
check(rp.status_code == 200 and _profile_row(UID1)["guide"] == "maia",
      "12c PATCH guide=maia : app_profiles.guide devient maia")

# la consultation active garde selena
slot = A.open_or_get_consultation(UID1, "maia", now=now + timedelta(minutes=5))
check(slot["status"] == "active" and slot["consultation"]["id"] == _cid
      and slot["consultation"]["advisor_id"] == "selena",
      "12d consultation active : advisor_id reste selena")

# message suivant : le cerveau continue avec Séléna, PAS Maïa
with patch.object(A, "call_llm", return_value=REPLY) as m2, \
     patch("auryel_bot.random.random", return_value=1.0):
    client.post("/api/consultation/message", json={"message": "et maintenant ?"},
                headers={"Authorization": f"Bearer {tok}"})
_sys_next = m2.call_args[0][0][0]["content"]
check(A.GUIDES["selena"]["nom"] in _sys_next and A.GUIDES["maia"]["nom"] not in _sys_next,
      "12e message suivant : persona reste Séléna (advisor figé), jamais Maïa")

# --- 13. routes enregistrées -------------------------------------
_methods = set()
for r in A.app.url_map.iter_rules():
    if r.rule == "/api/app/profile":
        _methods |= {m for m in r.methods if m in ("GET", "PATCH", "POST", "PUT", "DELETE")}
check(_methods == {"GET", "PATCH"},
      "13 /api/app/profile expose GET + PATCH uniquement (pas de POST/PUT/DELETE)")

# --- 14. legacy get_reply(phone, ...) intact -------------------
import inspect
check(str(inspect.signature(A.get_reply)) ==
      "(phone, user_message, depuis_pub=False, user_msg_pre_inserted=False)",
      "14 get_reply legacy : signature inchangée")

print("-" * 64)
_total = _STATE["pass"] + _STATE["fail"]
if _STATE["fail"] == 0:
    print(f"✅ {_STATE['pass']}/{_total} tests passés")
    sys.exit(0)
else:
    print(f"❌ {_STATE['fail']} test(s) en échec sur {_total}")
    sys.exit(1)
