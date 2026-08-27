"""
test_app_chat.py — B3.2 : POST /api/consultation/message -> cerveau Auryel (sans phone).

100 % local : psycopg2 mocké, get_conn -> fausse DB en mémoire, call_llm mocké,
aucun réseau, aucune vraie API OpenAI. Style aligné sur test_auth.py.
"""

import sys
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

import hashlib
import inspect

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
# Fausse base de données en mémoire
# ---------------------------------------------------------------------------
FAKE = {"accounts": [], "app_profiles": [], "app_sessions": [], "messages": [],
        "users": [], "seq": [0]}


def reset_db():
    for key in ("accounts", "app_profiles", "app_sessions", "messages", "users"):
        FAKE[key].clear()
    FAKE["seq"][0] = 0


def seed_account(user_id, deleted_at=None, email="u@example.com"):
    FAKE["accounts"].append({"user_id": user_id, "email": email, "deleted_at": deleted_at})


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

        # ---- accounts ----
        if k == "SELECT deleted_at FROM accounts WHERE user_id=%s":
            (uid,) = p
            r = next((a for a in FAKE["accounts"] if a["user_id"] == uid), None)
            self._result = (r["deleted_at"],) if r else None
        elif k == "SELECT email FROM accounts WHERE user_id=%s":
            (uid,) = p
            r = next((a for a in FAKE["accounts"] if a["user_id"] == uid), None)
            self._result = (r["email"],) if r else None

        # ---- app_sessions (B2.1) ----
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
        elif k == "INSERT INTO messages (user_id, phone, role, content, timestamp) VALUES (%s, NULL, %s, %s, %s)":
            uid, role, content, ts = p
            FAKE["seq"][0] += 1
            FAKE["messages"].append({"id": FAKE["seq"][0], "user_id": uid, "phone": None,
                                     "role": role, "content": content, "timestamp": ts})
        elif k == "SELECT role,content FROM messages WHERE user_id=%s ORDER BY id DESC LIMIT %s":
            uid, limit = p
            rows = sorted([m for m in FAKE["messages"] if m["user_id"] == uid],
                          key=lambda m: m["id"], reverse=True)
            self._rows = [(m["role"], m["content"]) for m in rows[:limit]]
        elif k == "SELECT role,content,timestamp FROM messages WHERE user_id=%s ORDER BY id ASC":
            (uid,) = p
            rows = sorted([m for m in FAKE["messages"] if m["user_id"] == uid], key=lambda m: m["id"])
            self._rows = [(m["role"], m["content"], m["timestamp"]) for m in rows]

        else:
            raise AssertionError("SQL non géré par le fake : " + k)


class FakeConn:
    def cursor(self):
        return FakeCursor()

    def commit(self):
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
REPLY = "Je te vois clairement, et je sens un mouvement autour de toi."


def fresh(uid=UID1):
    reset_db()
    seed_account(uid)
    return A.create_app_session(uid)


print("=" * 60)
print("TEST APP CHAT — B3.2  (POST /api/consultation/message)")
print("=" * 60)

# 1/2. auth
tok = fresh()
r = client.post("/api/consultation/message", json={"message": "bonjour"})
check(r.status_code == 401, "1 sans Bearer -> 401")
r = client.post("/api/consultation/message", json={"message": "bonjour"},
                headers={"Authorization": "Bearer not-a-token"})
check(r.status_code == 401, "2 Bearer invalide -> 401")

# 3/4/5/6. flux nominal
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY) as m_llm, \
     patch("auryel_bot.random.random", return_value=1.0):
    r = client.post("/api/consultation/message", json={"message": "j'ai besoin d'y voir clair"},
                    headers={"Authorization": f"Bearer {tok}"})
check(r.status_code == 200, "3 Bearer valide + message -> 200")
check(r.get_json().get("reply") == REPLY, "4 JSON contient 'reply'")
check(m_llm.call_count == 1, "5 call_llm appelé une fois")
_sys = m_llm.call_args[0][0][0]["content"]
check(A.GUIDES["selena"].get("nom", "selena") in _sys, "6 persona selena dans le system prompt")

# 7. changement de guide -> persona change
A.update_app_profile(UID1, guide="maia")
with patch.object(A, "call_llm", return_value=REPLY) as m_llm2, \
     patch("auryel_bot.random.random", return_value=1.0):
    client.post("/api/consultation/message", json={"message": "et maintenant"},
                headers={"Authorization": f"Bearer {tok}"})
_sys2 = m_llm2.call_args[0][0][0]["content"]
check(A.GUIDES["maia"].get("nom", "maia") in _sys2 and _sys2 != _sys,
      "7 changement de guide dans app_profiles -> persona différent")

# 8/9/10/11. persistance messages
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY):
    client.post("/api/consultation/message", json={"message": "coucou"},
                headers={"Authorization": f"Bearer {tok}"})
_msgs = [m for m in FAKE["messages"]]
check(any(m["role"] == "user" and m["content"] == "coucou" and m["user_id"] == UID1 for m in _msgs),
      "8 message user stocké avec messages.user_id")
check(any(m["role"] == "assistant" and m["content"] == REPLY and m["user_id"] == UID1 for m in _msgs),
      "9 réponse assistant stockée avec messages.user_id")
check(all(m["phone"] is None for m in _msgs), "10 messages.phone reste NULL")
check(A.get_history_for_user_id(UID1) ==
      [{"role": "user", "content": "coucou"}, {"role": "assistant", "content": REPLY}],
      "11 historique app contient le tour (user puis assistant)")

# 12. jamais mélangé avec un autre user_id
seed_account(UID2)
tok2 = A.create_app_session(UID2)
with patch.object(A, "call_llm", return_value="autre"):
    client.post("/api/consultation/message", json={"message": "moi c'est différent"},
                headers={"Authorization": f"Bearer {tok2}"})
check(len(A.get_history_for_user_id(UID1)) == 2
      and A.get_history_for_user_id(UID2) ==
      [{"role": "user", "content": "moi c'est différent"}, {"role": "assistant", "content": "autre"}],
      "12 historiques de deux user_id jamais mélangés")

# 13. nb_echanges augmente
check(A.get_app_profile(UID1)["nb_echanges"] == 1, "13 nb_echanges passe à 1 après un tour")

# 14/15/16/17/18/19. ce que l'app NE fait PAS
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY), \
     patch.object(A, "create_user") as m_cu, \
     patch.object(A, "get_stripe_links") as m_sl, \
     patch.object(A, "send_message") as m_sm, \
     patch.object(A, "check_and_increment_daily_limit") as m_dl, \
     patch.object(A, "gerer_onboarding") as m_ob:
    client.post("/api/consultation/message", json={"message": "je veux payer, donne-moi le lien"},
                headers={"Authorization": f"Bearer {tok}"})
check(len(FAKE["users"]) == 0, "14 aucune ligne users créée")
check(m_cu.call_count == 0, "15 create_user jamais appelé côté app")
check(m_sl.call_count == 0, "16 get_stripe_links jamais appelé côté app")
check(m_sm.call_count == 0, "17 send_message WhatsApp jamais appelé côté app")
check(m_dl.call_count == 0, "18 check_and_increment_daily_limit jamais appelé côté app")
check(m_ob.call_count == 0, "19 gerer_onboarding legacy jamais appelé côté app")

# 20/21. contexte émotionnel + garde-fou détresse
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY) as m_llm3, \
     patch("auryel_bot.random.random", return_value=1.0):
    client.post("/api/consultation/message",
                json={"message": "j'ai envie de mourir, je n'en peux plus"},
                headers={"Authorization": f"Bearer {tok}"})
_prof = A.get_app_profile(UID1)
check(_prof["niveau_detresse"] not in ("", "0") and _prof["dernier_signal_aigu_at"] != "",
      "20 contexte émotionnel app persisté (niveau_detresse + dernier_signal_aigu_at)")
_sys3 = m_llm3.call_args[0][0][0]["content"]
check("3114" in _sys3,
      "21 garde-fou détresse atteint get_system_prompt (prompt 3114) côté app")

# 22. body user_id malveillant ignoré
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY):
    r = client.post("/api/consultation/message",
                    json={"message": "identité falsifiée ?", "user_id": UID2},
                    headers={"Authorization": f"Bearer {tok}"})
check(r.status_code == 200
      and all(m["user_id"] == UID1 for m in FAKE["messages"])
      and not any(m["user_id"] == UID2 for m in FAKE["messages"]),
      "22 user_id du body ignoré, tout est rattaché au user_id de la session")

# 23/24. validation message
tok = fresh()
r = client.post("/api/consultation/message", json={"message": "   "},
                headers={"Authorization": f"Bearer {tok}"})
check(r.status_code == 400, "23 message vide -> 400")
r = client.post("/api/consultation/message", json={"message": "x" * 4001},
                headers={"Authorization": f"Bearer {tok}"})
check(r.status_code == 400, "24 message > 4000 caractères -> 400")

# 25. Cache-Control no-store
tok = fresh()
with patch.object(A, "call_llm", return_value=REPLY):
    r = client.post("/api/consultation/message", json={"message": "et voilà"},
                    headers={"Authorization": f"Bearer {tok}"})
check("no-store" in r.headers.get("Cache-Control", ""), "25 réponse -> Cache-Control: no-store")

# 26. rate-limit présent (anti-abus technique)
tok = fresh()
A.app.config["RATELIMIT_ENABLED"] = True
A.limiter.enabled = True
try:
    A.limiter.reset()
except Exception:
    pass
with patch.object(A, "call_llm", return_value=REPLY):
    _codes = [client.post("/api/consultation/message", json={"message": "spam"},
                          headers={"Authorization": f"Bearer {tok}"}).status_code
              for _ in range(31)]
A.app.config["RATELIMIT_ENABLED"] = False
A.limiter.enabled = False
try:
    A.limiter.reset()
except Exception:
    pass
check(_codes.count(200) == 30 and _codes[-1] == 429,
      "26 rate-limit anti-abus : 30 OK puis 429")

# 27/28. get_reply legacy préservé
check(str(inspect.signature(A.get_reply)) ==
      "(phone, user_message, depuis_pub=False, user_msg_pre_inserted=False)",
      "27 get_reply : signature legacy inchangée")
_src = inspect.getsource(A.get_reply)
check("get_user(phone)" in _src and "check_and_increment_daily_limit(phone" in _src
      and "gerer_onboarding(phone" in _src,
      "28 get_reply legacy continue d'utiliser phone (quota + onboarding)")

# 29. routes auth B2
_rules = {r.rule for r in A.app.url_map.iter_rules()}
check({"/api/auth/request-code", "/api/auth/verify-code", "/api/auth/logout",
       "/api/account"}.issubset(_rules), "29 routes auth B2 toujours présentes")

# 30. WhatsApp / Stripe / Telegram toujours présents
check({"/webhook", "/stripe/webhook", "/webhook/telegram"}.issubset(_rules),
      "30 routes WhatsApp / Stripe / Telegram toujours présentes")

# ---------------------------------------------------------------------------
print()
_total = _STATE["pass"] + _STATE["fail"]
if _STATE["fail"] == 0:
    print(f"✅ {_STATE['pass']}/{_total} tests passés")
    sys.exit(0)
else:
    print(f"❌ {_STATE['fail']} test(s) en échec sur {_total}")
    sys.exit(1)
