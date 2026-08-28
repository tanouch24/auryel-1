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
                 first_consultation_used_at=None):
    FAKE["accounts"].append({"user_id": user_id, "email": email,
                             "deleted_at": deleted_at,
                             "first_consultation_used_at": first_consultation_used_at})


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


def _expire_active_consultations():
    past = A._utcnow() - timedelta(hours=1)
    for c in FAKE["consultations"]:
        c["expires_at"] = past


print("=" * 64)
print("TEST APP CONSULTATION — B4.2  (route message + route state)")
print("=" * 64)

# --- 1. premier message premium : ouverture + débit + LLM + messages liés -------
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY) as m_llm, \
     patch("auryel_bot.random.random", return_value=1.0):
    r = _post(tok)
j = r.get_json()
check(r.status_code == 200, "1a premier message premium -> 200")
check(j["reply"] == REPLY and m_llm.call_count == 1, "1b reply renvoyé, call_llm appelé 1x")
check(j["consultation"]["opened_now"] is True, "1c opened_now = true")
check(j["consultation"]["credit_source"] == "monthly", "1d credit_source = monthly")
check(j["consultation"]["advisor_id"] == "selena", "1e advisor_id = selena (guide du profil)")
check(j["quota"]["monthly_used"] == 1 and j["quota"]["monthly_remaining"] == 3,
      "1f monthly_used 0 -> 1, remaining 3 (quota 4)")
check(j["quota"]["is_premium"] is True, "1g is_premium = true")
_cid = j["consultation"]["id"]
_msgs = FAKE["messages"]
check(any(m["role"] == "user" and m["content"] == "j'ai besoin d'y voir clair"
          and m["consultation_id"] == _cid for m in _msgs),
      "1h message user rattaché à consultation_id")
check(any(m["role"] == "assistant" and m["content"] == REPLY
          and m["consultation_id"] == _cid for m in _msgs),
      "1i message assistant rattaché au MÊME consultation_id")
check("no-store" in r.headers.get("Cache-Control", ""), "1j Cache-Control: no-store")

# --- 2. deuxième message < 2 h : même consultation, aucun débit, opened_now=false
with patch.object(A, "call_llm", return_value=REPLY):
    r2 = _post(tok, "et ensuite ?")
j2 = r2.get_json()
check(r2.status_code == 200 and j2["consultation"]["id"] == _cid,
      "2a 2e message -> même consultation")
check(j2["consultation"]["opened_now"] is False, "2b opened_now = false")
check(j2["quota"]["monthly_used"] == 1, "2c aucun nouveau débit (monthly_used reste 1)")
check(len(FAKE["consultations"]) == 1, "2d une seule consultation en base")
check(all(m["consultation_id"] == _cid for m in FAKE["messages"]),
      "2e tous les messages de la session portent le même consultation_id")
check(isinstance(j2["consultation"]["seconds_remaining"], int)
      and 0 < j2["consultation"]["seconds_remaining"] <= 7200,
      "2f seconds_remaining recalculé côté serveur (0 < x <= 7200)")

# --- 3. no_credit : 402, aucun LLM, aucun message, aucun contexte émotionnel ----
tok = fresh(premium=False)           # ni allowance ni earned credit
with patch.object(A, "call_llm", return_value=REPLY) as m_llm3, \
     patch.object(A, "_app_persist_emotional_context") as m_emo:
    r3 = _post(tok)
j3 = r3.get_json()
check(r3.status_code == 402, "3a sans crédit -> HTTP 402")
check(j3["error"] == "no_credit" and j3["consultation"] is None, "3b payload { error, consultation:null }")
check(j3["quota"]["is_premium"] is False and j3["quota"]["monthly_remaining"] == 0,
      "3c quota présent : is_premium=false, remaining 0")
check(m_llm3.call_count == 0, "3d call_llm jamais appelé")
check(m_emo.call_count == 0, "3e _app_persist_emotional_context jamais appelé")
check(len(FAKE["messages"]) == 0 and len(FAKE["consultations"]) == 0,
      "3f aucun message, aucune consultation créés")
check("no-store" in r3.headers.get("Cache-Control", ""), "3g 402 -> Cache-Control: no-store")

# --- 4. earned credit : ouvre une consultation ---------------------------------
tok = fresh(premium=False)
A.grant_earned_credit(UID1, "referral")
with patch.object(A, "call_llm", return_value=REPLY):
    r4 = _post(tok)
j4 = r4.get_json()
check(r4.status_code == 200 and j4["consultation"]["credit_source"] == "referral",
      "4a earned credit -> ouverture via referral")
check(j4["quota"]["is_premium"] is False and j4["quota"]["earned_available"] == 0,
      "4b gratuit : is_premium=false, earned_available 1 -> 0")

# --- 5. utilisateur gratuit sans earned -> 402 --------------------------------
tok = fresh(premium=False)
with patch.object(A, "call_llm", return_value=REPLY) as m_llm5:
    r5 = _post(tok)
check(r5.status_code == 402 and m_llm5.call_count == 0,
      "5 gratuit sans earned -> 402, aucun LLM")

# --- 6. conseiller FIGÉ 2 h sans modifier app_profiles.guide -----------------
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY) as m_llm6a, \
     patch("auryel_bot.random.random", return_value=1.0):
    _post(tok, "bonjour selena")
_sys_a = m_llm6a.call_args[0][0][0]["content"]
A.update_app_profile(UID1, guide="maia")          # l'app change de conseiller pendant la session
with patch.object(A, "call_llm", return_value=REPLY) as m_llm6b, \
     patch("auryel_bot.random.random", return_value=1.0):
    r6 = _post(tok, "et là ?")
_sys_b = m_llm6b.call_args[0][0][0]["content"]
check(A.GUIDES["selena"].get("nom", "selena") in _sys_a
      and A.GUIDES["selena"].get("nom", "selena") in _sys_b
      and A.GUIDES["maia"].get("nom", "maia") not in _sys_b,
      "6a persona reste selena sur les 2 tours malgré guide=maia")
check(r6.get_json()["consultation"]["advisor_id"] == "selena", "6b consultation.advisor_id = selena")
check(A.get_app_profile(UID1)["guide"] == "maia",
      "6c app_profiles.guide vaut maia (jamais réécrit par la route)")

# --- 7. GET /api/consultation/state -----------------------------------------
# 7a. inactif (aucune consultation, allowance active)
tok = fresh()
rs = client.get("/api/consultation/state", headers={"Authorization": f"Bearer {tok}"})
js = rs.get_json()
check(rs.status_code == 200 and js["consultation"] is None, "7a state : consultation = null si inactif")
check(js["quota"]["is_premium"] is True and js["quota"]["monthly_used"] == 0
      and js["quota"]["monthly_remaining"] == 4, "7b state : quota renvoyé (4), rien consommé")
check(len(FAKE["consultations"]) == 0, "7c state n'ouvre AUCUNE consultation")

# 7d. actif après un message
with patch.object(A, "call_llm", return_value=REPLY):
    _post(tok, "on commence")
A.grant_earned_credit(UID1, "share_30d")
rs = client.get("/api/consultation/state", headers={"Authorization": f"Bearer {tok}"})
js = rs.get_json()
check(js["consultation"] is not None and js["consultation"]["advisor_id"] == "selena",
      "7d state : consultation active renvoyée")
check(isinstance(js["consultation"]["seconds_remaining"], int)
      and 0 < js["consultation"]["seconds_remaining"] <= 7200,
      "7e state : seconds_remaining cohérent")
check(js["quota"]["monthly_used"] == 1 and js["quota"]["earned_available"] == 1,
      "7f state : quota + earned credits reflétés, aucune consommation par le GET")
_snap = (len(FAKE["consultations"]), FAKE["consultation_allowance"][0]["monthly_used"],
         sum(1 for e in FAKE["earned_credits"] if e["consumed_at"] is None))
client.get("/api/consultation/state", headers={"Authorization": f"Bearer {tok}"})
check((len(FAKE["consultations"]), FAKE["consultation_allowance"][0]["monthly_used"],
       sum(1 for e in FAKE["earned_credits"] if e["consumed_at"] is None)) == _snap,
      "7g deux appels state consécutifs : état inchangé")

# --- 8. session expirée : le message suivant tente une NOUVELLE ouverture -----
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY):
    r8a = _post(tok, "premier")
_cid8 = r8a.get_json()["consultation"]["id"]
_expire_active_consultations()
with patch.object(A, "call_llm", return_value=REPLY):
    r8b = _post(tok, "deuxième, bien plus tard")
j8b = r8b.get_json()
check(j8b["consultation"]["id"] != _cid8 and j8b["consultation"]["opened_now"] is True,
      "8a session expirée -> nouvelle consultation ouverte")
check(j8b["quota"]["monthly_used"] == 2, "8b nouveau débit (monthly_used 1 -> 2)")

# --- 9. payload JSON exact / cohérent --------------------------------------
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY):
    j9 = _post(tok).get_json()
check(set(j9.keys()) == {"reply", "consultation", "quota"}, "9a clés racine = reply/consultation/quota")
check(set(j9["consultation"].keys()) == {"id", "advisor_id", "started_at", "expires_at",
                                          "seconds_remaining", "credit_source", "opened_now"},
      "9b clés consultation exactes")
check(set(j9["quota"].keys()) == {"is_premium", "monthly_limit", "monthly_used",
                                   "monthly_remaining", "earned_available",
                                   "first_free_available",
                                   "period_start", "period_end"},
      "9c clés quota exactes (dont first_free_available)")
check(j9["quota"]["monthly_limit"] - j9["quota"]["monthly_used"] == j9["quota"]["monthly_remaining"],
      "9d monthly_remaining cohérent avec limit - used")
check(isinstance(j9["consultation"]["started_at"], str)
      and isinstance(j9["consultation"]["expires_at"], str),
      "9e started_at / expires_at sérialisés en ISO string")

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

# --- 15. PREMIÈRE CONSULTATION OFFERTE via les routes (Q2.1) --------------
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

# 15b. 1er POST /message -> 200, credit_source=first_free, marque posée
with patch.object(A, "call_llm", return_value=REPLY) as m_llm15:
    r15 = _post(tok)
j15 = r15.get_json()
check(r15.status_code == 200 and m_llm15.call_count == 1
      and j15["consultation"]["credit_source"] == "first_free"
      and j15["consultation"]["opened_now"] is True,
      "15b 1er message -> 200, credit_source = first_free")
check(j15["quota"]["first_free_available"] is False
      and j15["quota"]["monthly_used"] == 0,
      "15b2 first_free_available passe à false, monthly_used reste 0")
check(FAKE["accounts"][0]["first_consultation_used_at"] is not None,
      "15b3 accounts.first_consultation_used_at renseigné")

# 15c. reprise pendant les 2 h -> même id, aucune 2e consommation
with patch.object(A, "call_llm", return_value=REPLY):
    j15c = _post(tok, "suite").get_json()
check(j15c["consultation"]["id"] == j15["consultation"]["id"]
      and j15c["consultation"]["opened_now"] is False
      and len(FAKE["consultations"]) == 1,
      "15c 2e message < 2 h -> même consultation gratuite, opened_now false")

# 15d. après consommation + sans autre droit -> 402 no_credit
_expire_active_consultations()
with patch.object(A, "call_llm", return_value=REPLY) as m_llm15d:
    r15d = _post(tok)
check(r15d.status_code == 402 and r15d.get_json()["error"] == "no_credit"
      and m_llm15d.call_count == 0
      and r15d.get_json()["quota"]["first_free_available"] is False,
      "15d gratuite épuisée + aucun autre droit -> 402 no_credit")

# 15e. Premium neuf : la gratuite passe AVANT le quota mensuel
tok = fresh(premium=True, first_free=True)
with patch.object(A, "call_llm", return_value=REPLY):
    j15e = _post(tok).get_json()
check(j15e["consultation"]["credit_source"] == "first_free"
      and j15e["quota"]["monthly_used"] == 0
      and j15e["quota"]["monthly_remaining"] == 4,
      "15e Premium neuf -> 1er message = first_free, monthly_used 0 (4 mensuelles intactes)")
_expire_active_consultations()
with patch.object(A, "call_llm", return_value=REPLY):
    j15f = _post(tok).get_json()
check(j15f["consultation"]["credit_source"] == "monthly"
      and j15f["quota"]["monthly_used"] == 1,
      "15f après la gratuite -> 1re Premium, monthly_used 0 -> 1")

print("-" * 64)
_total = _STATE["pass"] + _STATE["fail"]
if _STATE["fail"] == 0:
    print(f"✅ {_STATE['pass']}/{_total} tests passés")
    sys.exit(0)
else:
    print(f"❌ {_STATE['fail']} test(s) en échec sur {_total}")
    sys.exit(1)
