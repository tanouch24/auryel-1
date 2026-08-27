"""
test_auth.py — B2.1 : helpers d'auth email + code à usage unique.

100 % local, aucune vraie DB, aucun vrai email Resend :
- psycopg2 est mocké avant l'import de auryel_bot ;
- get_conn est remplacé par une fausse connexion en mémoire (FakeConn) ;
- le module `resend` est mocké dans sys.modules pour le test d'envoi.

Style aligné sur test_morning.py / test_guides.py (script, pas pytest).
"""

import sys
from unittest.mock import MagicMock, patch

# Mock psycopg2 avant l'import de auryel_bot (pas de PostgreSQL en local)
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

from datetime import datetime, timedelta
from contextlib import redirect_stdout
import hashlib
import inspect
import io
import re
import uuid

import auryel_bot as A

# ---------------------------------------------------------------------------
# Compteur de tests
# ---------------------------------------------------------------------------
_STATE = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _STATE["pass"] += 1
        print(f"✅ {label}")
    else:
        _STATE["fail"] += 1
        print(f"❌ {label}")


# ---------------------------------------------------------------------------
# Fausse base de données en mémoire (aucune connexion PostgreSQL réelle)
# ---------------------------------------------------------------------------
FAKE = {"accounts": [], "auth_codes": [], "app_sessions": [], "seq": [0]}


def reset_db():
    FAKE["accounts"].clear()
    FAKE["auth_codes"].clear()
    FAKE["app_sessions"].clear()
    FAKE["seq"][0] = 0


class FakeCursor:
    def __init__(self):
        self._result = None
        self.rowcount = -1

    def fetchone(self):
        return self._result

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = " ".join(sql.split())
        p = params
        self.rowcount = -1

        if k == "UPDATE auth_codes SET consumed_at=%s WHERE email_normalized=%s AND consumed_at IS NULL":
            ts, email = p
            for r in FAKE["auth_codes"]:
                if r["email_normalized"] == email and r["consumed_at"] is None:
                    r["consumed_at"] = ts

        elif k == ("INSERT INTO auth_codes (email_normalized, code_hash, expires_at, "
                   "created_at, request_ip_hash) VALUES (%s, %s, %s, %s, %s)"):
            email, code_hash, expires_at, created_at, ip = p
            FAKE["seq"][0] += 1
            FAKE["auth_codes"].append({
                "id": FAKE["seq"][0], "email_normalized": email, "code_hash": code_hash,
                "expires_at": expires_at, "attempts": 0, "consumed_at": None,
                "created_at": created_at, "request_ip_hash": ip, "user_id": None,
            })

        elif k == ("SELECT id, code_hash, expires_at, attempts, consumed_at FROM auth_codes "
                   "WHERE email_normalized=%s ORDER BY id DESC LIMIT 1"):
            (email,) = p
            rows = sorted(
                [r for r in FAKE["auth_codes"] if r["email_normalized"] == email],
                key=lambda r: r["id"], reverse=True,
            )
            r = rows[0] if rows else None
            self._result = (
                (r["id"], r["code_hash"], r["expires_at"], r["attempts"], r["consumed_at"])
                if r else None
            )

        elif k == "UPDATE auth_codes SET attempts = attempts + 1 WHERE id=%s":
            (cid,) = p
            for r in FAKE["auth_codes"]:
                if r["id"] == cid:
                    r["attempts"] += 1

        elif k == "UPDATE auth_codes SET consumed_at=%s WHERE id=%s":
            ts, cid = p
            for r in FAKE["auth_codes"]:
                if r["id"] == cid:
                    r["consumed_at"] = ts

        elif k == "UPDATE auth_codes SET consumed_at=%s WHERE id=%s AND consumed_at IS NULL":
            ts, cid = p
            n = 0
            for r in FAKE["auth_codes"]:
                if r["id"] == cid and r["consumed_at"] is None:
                    r["consumed_at"] = ts
                    n += 1
            self.rowcount = n

        elif k == "UPDATE auth_codes SET consumed_at=NULL WHERE id=%s":
            (cid,) = p
            for r in FAKE["auth_codes"]:
                if r["id"] == cid:
                    r["consumed_at"] = None

        elif k == "SELECT user_id, deleted_at FROM accounts WHERE email_normalized=%s":
            (email,) = p
            r = next((x for x in FAKE["accounts"] if x["email_normalized"] == email), None)
            self._result = (r["user_id"], r["deleted_at"]) if r else None

        elif k == ("INSERT INTO accounts (user_id, email, email_normalized, auth_provider, "
                   "created_at) VALUES (%s, %s, %s, 'email', %s)"):
            uid, email, email_norm, created_at = p
            FAKE["accounts"].append({
                "user_id": uid, "email": email, "email_normalized": email_norm,
                "auth_provider": "email", "created_at": created_at,
                "last_login_at": None, "deleted_at": None,
            })

        elif k == ("INSERT INTO app_sessions (id, user_id, token_hash, created_at, expires_at, "
                   "platform, device_label) VALUES (%s, %s, %s, %s, %s, %s, %s)"):
            sid, uid, th, created_at, expires_at, platform, device = p
            FAKE["app_sessions"].append({
                "id": sid, "user_id": uid, "token_hash": th, "created_at": created_at,
                "expires_at": expires_at, "revoked_at": None, "last_used_at": None,
                "platform": platform, "device_label": device,
            })

        elif k == ("SELECT s.id, s.user_id, s.expires_at, s.revoked_at, a.email, a.deleted_at "
                   "FROM app_sessions s JOIN accounts a ON a.user_id = s.user_id "
                   "WHERE s.token_hash=%s"):
            (th,) = p
            srow = next((x for x in FAKE["app_sessions"] if x["token_hash"] == th), None)
            if not srow:
                self._result = None
            else:
                arow = next((x for x in FAKE["accounts"] if x["user_id"] == srow["user_id"]), None)
                self._result = (
                    (srow["id"], srow["user_id"], srow["expires_at"], srow["revoked_at"],
                     arow["email"], arow["deleted_at"])
                    if arow else None
                )

        elif k == "UPDATE app_sessions SET last_used_at=%s WHERE id=%s":
            ts, sid = p
            for r in FAKE["app_sessions"]:
                if r["id"] == sid:
                    r["last_used_at"] = ts

        elif k == "UPDATE app_sessions SET revoked_at=%s WHERE token_hash=%s AND revoked_at IS NULL":
            ts, th = p
            for r in FAKE["app_sessions"]:
                if r["token_hash"] == th and r["revoked_at"] is None:
                    r["revoked_at"] = ts

        elif k == "SELECT COUNT(*) FROM auth_codes WHERE email_normalized=%s AND created_at >= %s":
            email, since = p
            n = sum(1 for r in FAKE["auth_codes"]
                    if r["email_normalized"] == email and r["created_at"] >= since)
            self._result = (n,)

        elif k == "UPDATE accounts SET last_login_at=%s WHERE user_id=%s":
            ts, uid = p
            for r in FAKE["accounts"]:
                if r["user_id"] == uid:
                    r["last_login_at"] = ts

        elif k == "SELECT email, created_at, last_login_at FROM accounts WHERE user_id=%s":
            (uid,) = p
            r = next((x for x in FAKE["accounts"] if x["user_id"] == uid), None)
            self._result = (r["email"], r["created_at"], r["last_login_at"]) if r else None

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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
print("=" * 60)
print("TEST AUTH — B2.1 (schéma + helpers, aucun endpoint)")
print("=" * 60)

# 1. Normalisation email
check(A._normalize_email("  Foo.Bar@Example.COM ") == "foo.bar@example.com",
      "1a normalisation : strip + lower")
check(A._normalize_email("ﬀoo@example.com") == "ffoo@example.com",
      "1b normalisation : NFKC (ligature ﬀ -> ff)")
check(A._normalize_email("pas-un-email") is None, "1c entrée invalide -> None")
check(A._normalize_email("a@b") is None, "1d email sans domaine complet -> None")
check(A._normalize_email(None) is None, "1e None -> None")

# 2. Code = exactement 6 chiffres
reset_db()
code2 = A.create_auth_code("u2@example.com")
check(isinstance(code2, str) and len(code2) == 6 and code2.isdigit(),
      "2 code OTP = exactement 6 chiffres")

# 3. Zéros initiaux conservés + générations multiples
with patch("auryel_bot.secrets.randbelow", return_value=7):
    reset_db()
    code3 = A.create_auth_code("zero@example.com")
check(code3 == "000007", "3a zéros initiaux conservés (000007)")
reset_db()
gens = {A.create_auth_code("multi@example.com") for _ in range(6)}
check(all(len(x) == 6 and x.isdigit() for x in gens),
      "3b générations multiples toutes valides")

# 4. Code clair != code_hash
h4 = A._hash_auth_code("h@example.com", "123456")
check(h4 != "123456" and re.fullmatch(r"[0-9a-f]{64}", h4) is not None,
      "4 code_hash = HMAC-SHA256 hex(64), différent du code clair")

# 5. Même code, deux emails -> deux HMAC différents
check(A._hash_auth_code("a@example.com", "123456") != A._hash_auth_code("b@example.com", "123456"),
      "5 HMAC lié à l'email (2 emails -> 2 hash)")

# 6. HMAC correct accepté
reset_db()
good6 = A.create_auth_code("ok@example.com")
check(A.verify_auth_code("ok@example.com", good6) is True, "6 code correct accepté")

# 7. HMAC incorrect refusé + attempts incrémenté
reset_db()
A.create_auth_code("bad@example.com")
check(A.verify_auth_code("bad@example.com", "000000") is False, "7a code incorrect refusé")
check(FAKE["auth_codes"][-1]["attempts"] == 1, "7b échec incrémente attempts")

# 8. Code expiré (> 10 min) refusé
reset_db()
exp8 = A.create_auth_code("exp@example.com")
FAKE["auth_codes"][-1]["expires_at"] = datetime.utcnow() - timedelta(minutes=1)
check(A.verify_auth_code("exp@example.com", exp8) is False, "8 code expiré refusé")

# 9. 5 tentatives maximum
reset_db()
good9 = A.create_auth_code("lock@example.com")
for _ in range(5):
    A.verify_auth_code("lock@example.com", "999999")
check(FAKE["auth_codes"][-1]["attempts"] == 5, "9a 5 échecs comptés")
check(A.verify_auth_code("lock@example.com", good9) is False,
      "9b code correct refusé après 5 tentatives")

# 10. Code consommé non réutilisable
reset_db()
good10 = A.create_auth_code("once@example.com")
check(A.verify_auth_code("once@example.com", good10) is True, "10a 1re validation OK")
check(A.verify_auth_code("once@example.com", good10) is False, "10b code déjà consommé refusé")

# 11. Anciens codes invalidés à la nouvelle demande
reset_db()
A.create_auth_code("rot@example.com")
new11 = A.create_auth_code("rot@example.com")
check(FAKE["auth_codes"][0]["consumed_at"] is not None, "11a ancien code marqué consommé")
check(A.verify_auth_code("rot@example.com", new11) is True, "11b nouveau code valide")

# 12. Même email -> même user_id
reset_db()
uid_a = A.get_or_create_account("acc@example.com")
uid_b = A.get_or_create_account("acc@example.com")
check(uid_a == uid_b, "12a même email -> même user_id")
check(len(FAKE["accounts"]) == 1, "12b un seul compte créé")

# 13. user_id = UUID v4 valide
check(uuid.UUID(uid_a).version == 4, "13 user_id = UUID v4 valide")

# 14. Jeton de session opaque (>= 256 bits)
reset_db()
uid14 = A.get_or_create_account("sess@example.com")
tok14 = A.create_app_session(uid14)
check(isinstance(tok14, str) and len(tok14) >= 43 and re.fullmatch(r"[A-Za-z0-9_-]+", tok14) is not None,
      "14 jeton opaque url-safe, >= 256 bits d'entropie")

# 15. DB stocke le hash, pas le jeton clair
stored15 = FAKE["app_sessions"][-1]["token_hash"]
check(stored15 != tok14 and stored15 == hashlib.sha256(tok14.encode()).hexdigest(),
      "15 DB stocke SHA-256(jeton), jamais le jeton clair")

# 16. Session révoquée refusée
A.revoke_app_session(tok14)
check(A.resolve_app_session(tok14) is None, "16 session révoquée -> resolve = None")

# 17. Session expirée refusée
reset_db()
uid17 = A.get_or_create_account("exp2@example.com")
tok17 = A.create_app_session(uid17)
FAKE["app_sessions"][-1]["expires_at"] = datetime.utcnow() - timedelta(seconds=1)
check(A.resolve_app_session(tok17) is None, "17 session expirée -> resolve = None")

# 18. Compte deleted_at refusé
reset_db()
uid18 = A.get_or_create_account("del@example.com")
tok18 = A.create_app_session(uid18)
check(A.resolve_app_session(tok18) is not None, "18a session OK tant que le compte est vivant")
FAKE["accounts"][0]["deleted_at"] = datetime.utcnow()
check(A.get_or_create_account("del@example.com") is None,
      "18b get_or_create_account refuse un email soft-supprimé")
check(A.resolve_app_session(tok18) is None, "18c resolve refuse si le compte est supprimé")

# 19. send_email_code mocké (aucun appel réseau réel)
fake_resend = MagicMock()
with patch.dict("sys.modules", {"resend": fake_resend}):
    res_ok = A.send_email_code("dest@example.com", "424242")
check(res_ok == {"ok": True, "error": None}, "19a send_email_code : statut OK (mock)")
check(fake_resend.Emails.send.call_count == 1, "19b Resend appelé exactement une fois")
_payload = fake_resend.Emails.send.call_args[0][0]
check(_payload["to"] == ["dest@example.com"] and "424242" in _payload["html"],
      "19c destinataire correct + code présent uniquement dans le corps")
fake_resend_ko = MagicMock()
fake_resend_ko.Emails.send.side_effect = RuntimeError("boom")
with patch.dict("sys.modules", {"resend": fake_resend_ko}):
    res_ko = A.send_email_code("dest@example.com", "424242")
check(res_ko["ok"] is False and res_ko["error"] == "send_failed",
      "19d échec Resend -> statut exploitable (erreur non avalée)")


# ===========================================================================
# ENDPOINTS B2.2 — via le client de test Flask (DB toujours mockée, aucun réseau)
# ===========================================================================
print()
print("-" * 60)
print("ENDPOINTS /api/auth/* + /api/account")
print("-" * 60)

A.app.config["TESTING"] = True
A.app.config["RATELIMIT_ENABLED"] = False   # désactivé par défaut, réactivé pour E4
A.limiter.enabled = False                    # idem, via l'API Flask-Limiter
client = A.app.test_client()

_OK_SEND = {"ok": True, "error": None}


def _capturing_send(store, key="c"):
    """Fabrique un faux send_email_code qui mémorise le code envoyé."""
    def _f(email, code):
        store[key] = code
        return _OK_SEND
    return _f


# ---- REQUEST CODE ----
reset_db()
with patch.object(A, "send_email_code", return_value=_OK_SEND):
    r = client.post("/api/auth/request-code", json={"email": "not-an-email"})
check(r.status_code == 400 and r.get_json() == {"error": "invalid_request"},
      "E1 request-code : email invalide -> 400")

reset_db()
with patch.object(A, "send_email_code", return_value=_OK_SEND) as m2:
    r = client.post("/api/auth/request-code", json={"email": "New.User@Example.com"})
check(r.status_code == 200 and r.get_json() == {"status": "ok"},
      "E2 request-code : email valide -> 200 {status: ok}")
check(m2.call_count == 1, "E2b request-code : send_email_code appelé une fois")

reset_db()
A.get_or_create_account("existing@example.com")   # compte déjà présent
with patch.object(A, "send_email_code", return_value=_OK_SEND):
    r_exist = client.post("/api/auth/request-code", json={"email": "existing@example.com"})
    r_new = client.post("/api/auth/request-code", json={"email": "brandnew@example.com"})
check(r_exist.status_code == r_new.status_code == 200
      and r_exist.get_json() == r_new.get_json() == {"status": "ok"},
      "E3 request-code : réponse identique compte existant / inexistant")

# limite IP (3 / 10 min) — activée uniquement le temps de ce test
reset_db()
A.app.config["RATELIMIT_ENABLED"] = True
A.limiter.enabled = True
try:
    A.limiter.reset()
except Exception:
    pass
with patch.object(A, "send_email_code", return_value=_OK_SEND):
    ip_status = [client.post("/api/auth/request-code",
                             json={"email": "iplimit@example.com"}).status_code
                 for _ in range(4)]
A.app.config["RATELIMIT_ENABLED"] = False
A.limiter.enabled = False
try:
    A.limiter.reset()
except Exception:
    pass
check(ip_status[:3] == [200, 200, 200] and ip_status[3] == 429,
      "E4 request-code : limite IP 3/10min -> 4e requête = 429")

# limite applicative par email (5 / heure)
reset_db()
with patch.object(A, "send_email_code", return_value=_OK_SEND) as m5:
    per_email = [client.post("/api/auth/request-code",
                             json={"email": "peremail@example.com"}).status_code
                 for _ in range(6)]
check(all(s == 200 for s in per_email) and m5.call_count == 5,
      "E5 request-code : plafond 5/h par email -> 6e sans envoi, toujours 200 générique")

# send_email_code reçoit (email, code 6 chiffres)
reset_db()
seen = {}
with patch.object(A, "send_email_code", side_effect=_capturing_send(seen, "code")):
    client.post("/api/auth/request-code", json={"email": "capture@example.com"})
    seen["email"] = "capture@example.com"
check(isinstance(seen.get("code"), str) and re.fullmatch(r"\d{6}", seen["code"]) is not None,
      "E6 request-code : send_email_code reçoit un code à 6 chiffres")

# échec Resend -> code invalidé + 503 générique
reset_db()
with patch.object(A, "send_email_code", return_value={"ok": False, "error": "send_failed"}):
    r = client.post("/api/auth/request-code", json={"email": "sendko@example.com"})
check(r.status_code == 503 and r.get_json() == {"error": "temporarily_unavailable"},
      "E7 request-code : échec Resend -> 503 temporarily_unavailable")
check(len(FAKE["auth_codes"]) == 1 and FAKE["auth_codes"][0]["consumed_at"] is not None,
      "E7b request-code : le code non livré est invalidé immédiatement")

# aucune fuite code / email / token dans stdout
reset_db()
leak = {}
LEAK_EMAIL = "leaktest@example.com"
_buf = io.StringIO()
with patch.object(A, "send_email_code", side_effect=_capturing_send(leak)), redirect_stdout(_buf):
    client.post("/api/auth/request-code", json={"email": LEAK_EMAIL})
    rv = client.post("/api/auth/verify-code", json={"email": LEAK_EMAIL, "code": leak["c"]})
    leak_tok = rv.get_json()["token"]
    client.get("/api/account", headers={"Authorization": f"Bearer {leak_tok}"})
    client.post("/api/auth/logout", headers={"Authorization": f"Bearer {leak_tok}"})
_out = _buf.getvalue()
check(leak["c"] not in _out and LEAK_EMAIL not in _out and leak_tok not in _out,
      "E8 aucun code / email / token en clair dans stdout")

# ---- VERIFY CODE ----
reset_db()
with patch.object(A, "send_email_code", return_value=_OK_SEND):
    client.post("/api/auth/request-code", json={"email": "v9@example.com"})
r = client.post("/api/auth/verify-code", json={"email": "v9@example.com"})
check(r.status_code == 400 and r.get_json() == {"error": "invalid_request"},
      "E9 verify-code : body sans code -> 400")
r = client.post("/api/auth/verify-code", json={"email": "v9@example.com", "code": "12ab56"})
check(r.status_code == 400, "E9b verify-code : code non numérique -> 400")

reset_db()
with patch.object(A, "send_email_code", return_value=_OK_SEND):
    client.post("/api/auth/request-code", json={"email": "v10@example.com"})
r = client.post("/api/auth/verify-code", json={"email": "v10@example.com", "code": "000000"})
check(r.status_code == 401 and r.get_json() == {"error": "invalid_code"},
      "E10 verify-code : mauvais code -> 401 invalid_code")

reset_db()
s11 = {}
with patch.object(A, "send_email_code", side_effect=_capturing_send(s11)):
    client.post("/api/auth/request-code", json={"email": "v11@example.com"})
FAKE["auth_codes"][-1]["expires_at"] = datetime.utcnow() - timedelta(minutes=1)
r = client.post("/api/auth/verify-code", json={"email": "v11@example.com", "code": s11["c"]})
check(r.status_code == 401 and r.get_json() == {"error": "invalid_code"},
      "E11 verify-code : code expiré -> 401")

reset_db()
s12 = {}
with patch.object(A, "send_email_code", side_effect=_capturing_send(s12)):
    client.post("/api/auth/request-code", json={"email": "v12@example.com"})
r = client.post("/api/auth/verify-code", json={"email": "v12@example.com", "code": s12["c"]})
body = r.get_json()
check(r.status_code == 200, "E12 verify-code : bon code -> 200")
check(body.get("token") and body.get("token_type") == "Bearer"
      and body.get("user_id") and body.get("expires_in") == 7776000,
      "E13 verify-code : token Bearer + user_id + expires_in 90j")
_uid_v12 = body["user_id"]

s12b = {}
with patch.object(A, "send_email_code", side_effect=_capturing_send(s12b)):
    client.post("/api/auth/request-code", json={"email": "v12@example.com"})
r2 = client.post("/api/auth/verify-code", json={"email": "v12@example.com", "code": s12b["c"]})
check(r2.status_code == 200 and r2.get_json()["user_id"] == _uid_v12,
      "E14 verify-code : reconnexion même email -> même user_id")

r3 = client.post("/api/auth/verify-code", json={"email": "v12@example.com", "code": s12b["c"]})
check(r3.status_code == 401, "E15 verify-code : code déjà consommé -> 401")

reset_db()
A.get_or_create_account("vdel@example.com")
FAKE["accounts"][0]["deleted_at"] = datetime.utcnow()
s16 = {}
with patch.object(A, "send_email_code", side_effect=_capturing_send(s16)):
    client.post("/api/auth/request-code", json={"email": "vdel@example.com"})
r = client.post("/api/auth/verify-code", json={"email": "vdel@example.com", "code": s16["c"]})
check(r.status_code == 401 and r.get_json() == {"error": "invalid_code"},
      "E16 verify-code : email d'un compte supprimé -> 401 générique")

reset_db()
s17 = {}
with patch.object(A, "send_email_code", side_effect=_capturing_send(s17)):
    client.post("/api/auth/request-code", json={"email": "v17@example.com"})
r = client.post("/api/auth/verify-code",
                json={"email": "v17@example.com", "code": s17["c"],
                      "platform": "android", "device_label": "Pixel 8"})
check(r.status_code == 200 and r.get_json().get("token"),
      "E17 verify-code : platform + device_label acceptés (facultatifs)")
check(FAKE["app_sessions"][-1]["platform"] == "android"
      and FAKE["app_sessions"][-1]["device_label"] == "Pixel 8",
      "E17b verify-code : platform / device_label persistés")

# ---- ACCOUNT ----
reset_db()
s20 = {}
with patch.object(A, "send_email_code", side_effect=_capturing_send(s20)):
    client.post("/api/auth/request-code", json={"email": "acc@example.com"})
rv = client.post("/api/auth/verify-code", json={"email": "acc@example.com", "code": s20["c"]})
TOK = rv.get_json()["token"]
UID = rv.get_json()["user_id"]

r = client.get("/api/account")
check(r.status_code == 401 and r.get_json() == {"error": "unauthorized"},
      "E18 account : sans Bearer -> 401")
r = client.get("/api/account", headers={"Authorization": "Bearer not-a-real-token"})
check(r.status_code == 401, "E19 account : Bearer invalide -> 401")
r = client.get("/api/account", headers={"Authorization": f"Bearer {TOK}"})
body = r.get_json()
check(r.status_code == 200 and body["user_id"] == UID and body["email"] == "acc@example.com",
      "E20 account : Bearer valide -> 200 + identité")

r = client.get("/api/account?user_id=00000000-0000-4000-8000-000000000000",
               headers={"Authorization": f"Bearer {TOK}"},
               json={"user_id": "11111111-1111-4111-8111-111111111111"})
check(r.status_code == 200 and r.get_json()["user_id"] == UID,
      "E21 account : user_id du body/query ignoré, identité = session")

check(set(body.keys()) == {"user_id", "email", "created_at", "last_login_at"},
      "E22 account : réponse minimale (aucun phone / Stripe / conversation)")

# ---- LOGOUT ----
r = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {TOK}"})
check(r.status_code == 200 and r.get_json() == {"status": "ok"},
      "E23 logout : 200 {status: ok}")
r = client.get("/api/account", headers={"Authorization": f"Bearer {TOK}"})
check(r.status_code == 401, "E24 logout : après révocation, /api/account refuse le token")

# ---- HEADERS Cache-Control: no-store ----
reset_db()
sh = {}
with patch.object(A, "send_email_code", side_effect=_capturing_send(sh)):
    rc = client.post("/api/auth/request-code", json={"email": "hdr@example.com"})
vc = client.post("/api/auth/verify-code", json={"email": "hdr@example.com", "code": sh["c"]})
htok = vc.get_json()["token"]
ac = client.get("/api/account", headers={"Authorization": f"Bearer {htok}"})
lo = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {htok}"})
bad = client.post("/api/auth/request-code", json={"email": "x"})
check(all("no-store" in resp.headers.get("Cache-Control", "") for resp in (rc, vc, ac, lo, bad)),
      "E25 réponses auth -> Cache-Control: no-store")

# ---- RÉGRESSION : rien d'autre n'a bougé ----
check(str(inspect.signature(A.get_reply)) ==
      "(phone, user_message, depuis_pub=False, user_msg_pre_inserted=False)",
      "E26 get_reply : signature inchangée")

_rules = {}
for _r in A.app.url_map.iter_rules():
    _rules.setdefault(_r.rule, set()).update(_r.methods)
check("/stripe/webhook" in _rules and "POST" in _rules["/stripe/webhook"]
      and "/stripe/create-checkout" in _rules and "POST" in _rules["/stripe/create-checkout"],
      "E27 routes Stripe (/stripe/webhook, /stripe/create-checkout) intactes")
check("/webhook" in _rules and "GET" in _rules["/webhook"] and "POST" in _rules["/webhook"],
      "E28 webhook WhatsApp /webhook (GET + POST) intact")
check("/webhook/telegram" in _rules and "POST" in _rules["/webhook/telegram"],
      "E29 route Telegram /webhook/telegram intacte")


# ===========================================================================
# FIX FINAL B2.2 — verify-code ne consomme le code qu'après création de session
# ===========================================================================
print()
print("-" * 60)
print("FIX verify-code : consommation du code après création de session")
print("-" * 60)

# E30 : échec get_or_create_account -> 503, code NON consommé, réessai possible
reset_db()
s30 = {}
with patch.object(A, "send_email_code", side_effect=_capturing_send(s30)):
    client.post("/api/auth/request-code", json={"email": "fix30@example.com"})
with patch.object(A, "get_or_create_account", side_effect=RuntimeError("db down")):
    r = client.post("/api/auth/verify-code", json={"email": "fix30@example.com", "code": s30["c"]})
check(r.status_code == 503 and r.get_json() == {"error": "temporarily_unavailable"},
      "E30 verify-code : échec création compte -> 503 générique")
check(FAKE["auth_codes"][-1]["consumed_at"] is None,
      "E30b verify-code : code NON consommé si la création du compte échoue")
r = client.post("/api/auth/verify-code", json={"email": "fix30@example.com", "code": s30["c"]})
check(r.status_code == 200 and r.get_json().get("token"),
      "E30c verify-code : le même code fonctionne au réessai après échec transitoire")

# E31 : échec create_app_session -> 503, code NON consommé, réessai possible
reset_db()
s31 = {}
with patch.object(A, "send_email_code", side_effect=_capturing_send(s31)):
    client.post("/api/auth/request-code", json={"email": "fix31@example.com"})
with patch.object(A, "create_app_session", side_effect=RuntimeError("insert failed")):
    r = client.post("/api/auth/verify-code", json={"email": "fix31@example.com", "code": s31["c"]})
check(r.status_code == 503, "E31 verify-code : échec création session -> 503 générique")
check(FAKE["auth_codes"][-1]["consumed_at"] is None,
      "E31b verify-code : code NON consommé si la création de session échoue")
r = client.post("/api/auth/verify-code", json={"email": "fix31@example.com", "code": s31["c"]})
check(r.status_code == 200 and r.get_json().get("token"),
      "E31c verify-code : le même code fonctionne au réessai après échec de session")

# E32 : succès complet -> consumed_at renseigné, exactement une session
reset_db()
s32 = {}
with patch.object(A, "send_email_code", side_effect=_capturing_send(s32)):
    client.post("/api/auth/request-code", json={"email": "fix32@example.com"})
r = client.post("/api/auth/verify-code", json={"email": "fix32@example.com", "code": s32["c"]})
check(r.status_code == 200, "E32 verify-code : succès complet -> 200")
check(FAKE["auth_codes"][-1]["consumed_at"] is not None,
      "E32b verify-code : consumed_at renseigné après création de session réussie")
check(len(FAKE["app_sessions"]) == 1, "E32c verify-code : exactement une session créée")

# E33 : après succès, le même code est refusé
r = client.post("/api/auth/verify-code", json={"email": "fix32@example.com", "code": s32["c"]})
check(r.status_code == 401 and r.get_json() == {"error": "invalid_code"},
      "E33 verify-code : code déjà consommé après succès -> 401")

# E34 : revendication atomique -> une seule des deux tentatives concurrentes gagne
reset_db()
s34 = {}
with patch.object(A, "send_email_code", side_effect=_capturing_send(s34)):
    client.post("/api/auth/request-code", json={"email": "race@example.com"})
_cid = A.check_auth_code("race@example.com", s34["c"])
check(_cid is not None, "E34 check_auth_code : code valide -> id retourné sans consommer")
check(FAKE["auth_codes"][-1]["consumed_at"] is None,
      "E34b check_auth_code : ne consomme pas le code")
check(A.claim_auth_code(_cid) is True, "E34c claim_auth_code : 1re revendication gagne")
check(A.claim_auth_code(_cid) is False, "E34d claim_auth_code : 2e revendication concurrente perd")
A.release_auth_code(_cid)
check(A.claim_auth_code(_cid) is True, "E34e release_auth_code : code de nouveau revendicable")

# ---------------------------------------------------------------------------
print()
_total = _STATE["pass"] + _STATE["fail"]
if _STATE["fail"] == 0:
    print(f"✅ {_STATE['pass']}/{_total} tests passés")
    sys.exit(0)
else:
    print(f"❌ {_STATE['fail']} test(s) en échec sur {_total}")
    sys.exit(1)
