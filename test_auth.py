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
import hashlib
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

    def fetchone(self):
        return self._result

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = " ".join(sql.split())
        p = params

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

# ---------------------------------------------------------------------------
print()
_total = _STATE["pass"] + _STATE["fail"]
if _STATE["fail"] == 0:
    print(f"✅ {_STATE['pass']}/{_total} tests passés")
    sys.exit(0)
else:
    print(f"❌ {_STATE['fail']} test(s) en échec sur {_total}")
    sys.exit(1)
