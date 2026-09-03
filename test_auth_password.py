"""
test_auth_password.py — AUTH V2 (Migration v36) : création de compte + connexion
par email + mot de passe. OTP NON REQUIS.

100 % local, aucune vraie DB, aucun réseau :
- psycopg2 mocké avant l'import de auryel_bot ;
- get_conn -> fausse connexion en mémoire (FakeConn) qui matche les requêtes
  SQL exactes émises par le code auth V2 ;
- Flask test_client, rate-limiter désactivé.

Style aligné sur test_auth.py / test_app_profile_api.py (script, pas pytest).
Sortie : compteur ✅/❌, exit(1) si au moins un échec.
"""

import sys
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

from datetime import datetime

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
# Fausse DB en mémoire — uniquement les tables touchées par l'auth V2.
# ---------------------------------------------------------------------------
FAKE = {"accounts": [], "app_sessions": []}


def reset_db():
    FAKE["accounts"].clear()
    FAKE["app_sessions"].clear()


def seed_account(email_normalized, password_hash=None, deleted_at=None):
    uid = f"uid-{email_normalized}"
    FAKE["accounts"].append({
        "user_id": uid, "email": email_normalized,
        "email_normalized": email_normalized, "password_hash": password_hash,
        "created_at": datetime.utcnow(), "last_login_at": None,
        "deleted_at": deleted_at,
    })
    return uid


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

        if k == "SELECT user_id, password_hash, deleted_at FROM accounts WHERE email_normalized=%s":
            (email,) = p
            r = next((x for x in FAKE["accounts"] if x["email_normalized"] == email), None)
            self._result = (r["user_id"], r["password_hash"], r["deleted_at"]) if r else None

        elif k == "SELECT user_id, deleted_at FROM accounts WHERE email_normalized=%s":
            (email,) = p
            r = next((x for x in FAKE["accounts"] if x["email_normalized"] == email), None)
            self._result = (r["user_id"], r["deleted_at"]) if r else None

        elif k == ("INSERT INTO accounts (user_id, email, email_normalized, auth_provider, "
                   "password_hash, created_at) VALUES (%s, %s, %s, 'email', %s, %s)"):
            uid, email, email_norm, pw_hash, created_at = p
            FAKE["accounts"].append({
                "user_id": uid, "email": email, "email_normalized": email_norm,
                "password_hash": pw_hash, "created_at": created_at,
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

        elif k == "UPDATE accounts SET last_login_at=%s WHERE user_id=%s":
            ts, uid = p
            for r in FAKE["accounts"]:
                if r["user_id"] == uid:
                    r["last_login_at"] = ts

        elif k == "UPDATE accounts SET password_hash=%s WHERE user_id=%s":
            pw_hash, uid = p
            for r in FAKE["accounts"]:
                if r["user_id"] == uid:
                    r["password_hash"] = pw_hash

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
                     arow["email"], arow["deleted_at"]) if arow else None
                )

        elif k == "UPDATE app_sessions SET last_used_at=%s WHERE id=%s":
            ts, sid = p
            for r in FAKE["app_sessions"]:
                if r["id"] == sid:
                    r["last_used_at"] = ts

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

A.app.config["TESTING"] = True
A.app.config["RATELIMIT_ENABLED"] = False
A.limiter.enabled = False
client = A.app.test_client()


def register(email, password, **extra):
    body = {"email": email, "password": password}
    body.update(extra)
    return client.post("/api/app/auth/register", json=body)


def login(email, password):
    return client.post("/api/app/auth/login", json={"email": email, "password": password})


print("=" * 64)
print("TEST AUTH V2 — email + mot de passe (Migration v36, OTP non requis)")
print("=" * 64)

# --- Migration : forme du DDL dans init_db() + miroir ---------------------
import re as _re
_SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "auryel_bot.py"),
            encoding="utf-8").read()
_m36 = _re.search(r"#\s*Migration v36\b", _SRC)
_m35 = _re.search(r"#\s*Migration v35\b", _SRC)
check(_m36 is not None and _m35 is not None and _m36.start() > _m35.start(),
      "MIG-1 bloc « Migration v36 » présent, après v35, dans init_db()")
_v36 = _SRC[_m36.start(): _SRC.find("conn.close()", _m36.start())]
check("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS password_hash TEXT" in _v36,
      "MIG-2 v36 = ADD COLUMN IF NOT EXISTS password_hash (idempotent, nullable)")
# On n'inspecte QUE les instructions exécutées (lignes c.execute...), pas la
# prose du commentaire (qui cite volontairement « aucun DROP / DELETE »).
_v36_exec = "\n".join(
    ln for ln in _v36.splitlines()
    if ln.strip().startswith("c.execute(") or ln.strip().startswith("conn.commit")
)
check(not _re.search(r"\b(DROP|DELETE|TRUNCATE)\b", _v36_exec)
      and "NOT NULL" not in _v36_exec,
      "MIG-3 v36 strictement additif (aucun DROP/DELETE/TRUNCATE/NOT NULL exécuté)")
_mirror_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "migrations", "010_accounts_password_hash.sql")
check(os.path.exists(_mirror_path), "MIG-4 miroir migrations/010_accounts_password_hash.sql présent")
if os.path.exists(_mirror_path):
    _mir = open(_mirror_path, encoding="utf-8").read()
    check("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS password_hash TEXT" in _mir,
          "MIG-5 miroir 010 aligné sur le DDL exécuté")

# --- A. register succès -------------------------------------------------------
reset_db()
r = register("Alice@Example.com", "correcthorsebattery")
check(r.status_code == 200, "A register email/password -> 200")
j = r.get_json()
check(bool(j.get("token")) and j.get("token_type") == "Bearer" and bool(j.get("user_id")),
      "A register renvoie token Bearer + user_id (même forme que verify-code)")
check(r.headers.get("Cache-Control") == "no-store", "A réponse register = Cache-Control: no-store")

# --- B. password_hash présent en base ---------------------------------------
acc = FAKE["accounts"][-1]
check(bool(acc["password_hash"]), "B password_hash stocké en base après register")
check(acc["email_normalized"] == "alice@example.com", "B email normalisé (trim + lower) en base")

# --- C. mot de passe brut jamais stocké -----------------------------------
check("correcthorsebattery" not in (acc["password_hash"] or ""),
      "C le mot de passe en clair n'apparaît pas dans password_hash")
check(acc["password_hash"].startswith("pbkdf2:sha256"),
      "C hash = werkzeug pbkdf2:sha256 (pas de SHA/MD5 maison, non réversible)")

# --- Q/R. aucune fuite password / hash dans la réponse --------------------
_raw = r.get_data(as_text=True)
check("correcthorsebattery" not in _raw, "Q aucune fuite du mot de passe dans la réponse register")
check("password_hash" not in _raw and (acc["password_hash"] not in _raw),
      "R aucune fuite du hash dans la réponse register")

# --- D. login correct -------------------------------------------------------
reset_db()
seed_uid = None
r = register("bob@example.com", "unlongmotdepasse")
seed_uid = r.get_json()["user_id"]
r = login("bob@example.com", "unlongmotdepasse")
check(r.status_code == 200, "D login mot de passe correct -> 200")
jl = r.get_json()
check(bool(jl.get("token")) and jl.get("user_id") == seed_uid, "D login renvoie une session pour le bon user_id")

# --- E. mauvais password -> 401 générique --------------------------------
r = login("bob@example.com", "mauvais-mot-de-passe")
check(r.status_code == 401, "E mauvais mot de passe -> 401")
check(r.get_json().get("error") == "invalid_credentials", "E code d'erreur stable = invalid_credentials")
check("incorrect" in (r.get_json().get("message") or "").lower(),
      "E message générique « Email ou mot de passe incorrect »")

# --- F. email inexistant -> MÊME erreur générique -----------------------
r2 = login("inconnu-total@example.com", "peu-importe-123")
check(r2.status_code == 401 and r2.get_json().get("error") == "invalid_credentials",
      "F email inexistant -> même 401 invalid_credentials (aucune fuite d'existence)")
check(r2.get_json() == r.get_json(),
      "F réponse identique email inexistant vs mauvais mot de passe")

# --- G. email dupliqué -> 409 --------------------------------------------
r = register("bob@example.com", "encoreunmotdepasse")
check(r.status_code == 409 and r.get_json().get("error") == "email_taken",
      "G register sur email déjà utilisé -> 409 email_taken")

# --- H. mot de passe < 8 -> 400 ----------------------------------------
reset_db()
r = register("court@example.com", "1234567")
check(r.status_code == 400 and r.get_json().get("error") == "weak_password",
      "H mot de passe < 8 caractères -> 400 weak_password")
check(FAKE["accounts"] == [], "H aucun compte créé quand le mot de passe est trop court")
# passphrase de 8+ acceptée (pas de règle majuscule/chiffre/symbole)
r = register("court@example.com", "mot de passe simple")
check(r.status_code == 200, "H passphrase (espaces, minuscules) acceptée dès 8 caractères")

# --- I. email invalide -> 400 ---------------------------------------------
reset_db()
r = register("pas-un-email", "motdepassevalide")
check(r.status_code == 400 and r.get_json().get("error") == "invalid_email",
      "I email invalide -> 400 invalid_email")

# --- J + K. session utilisable + endpoint protégé après login -----------
reset_db()
register("carol@example.com", "motdepassedecarol")
tok = login("carol@example.com", "motdepassedecarol").get_json()["token"]
r = client.get("/api/account", headers={"Authorization": f"Bearer {tok}"})
check(r.status_code == 200, "K endpoint protégé /api/account -> 200 avec le token du login V2")
check(r.get_json().get("email") == "carol@example.com",
      "K /api/account renvoie bien le compte connecté (identité issue du Bearer)")
check("password" not in r.get_data(as_text=True), "K /api/account ne fuit aucun champ password")
r = client.get("/api/account", headers={"Authorization": "Bearer faux-token"})
check(r.status_code == 401, "J token invalide -> 401 sur endpoint protégé")

# --- L. compte deleted_at ne peut pas login --------------------------
reset_db()
seed_account("deleted@example.com",
             password_hash=A.generate_password_hash("motdepasse-supprime",
                                                    method=A._PASSWORD_HASH_METHOD),
             deleted_at=datetime.utcnow())
r = login("deleted@example.com", "motdepasse-supprime")
check(r.status_code == 401 and r.get_json().get("error") == "invalid_credentials",
      "L compte soft-supprimé -> 401 générique (aucune fuite de suppression)")

# --- M. ancien compte OTP sans password_hash ------------------------------
reset_db()
seed_account("legacy-otp@example.com", password_hash=None)
r = login("legacy-otp@example.com", "un-mot-de-passe-quelconque")
check(r.status_code == 409 and r.get_json().get("error") == "password_not_set",
      "M compte legacy sans mot de passe -> 409 password_not_set (stable, aucun mdp inventé)")
# register sur ce même email : refusé (compte existe déjà) -> 409, pas de casse
r = register("legacy-otp@example.com", "nouveau-mot-de-passe")
check(r.status_code == 409 and r.get_json().get("error") == "email_taken",
      "M register sur un email legacy existant -> 409 (compte OTP préservé, non écrasé)")
check(FAKE["accounts"][0]["password_hash"] is None,
      "M le compte legacy n'a PAS été modifié (password_hash toujours NULL)")

# --- N/O. OTP non requis pour register/login V2 -------------------------
reset_db()
_before = list(getattr(A, "FAKE", {}).get("auth_codes", []))  # n/a ici : pas de table auth_codes touchée
r = register("noopt@example.com", "motdepassesansotp")
check(r.status_code == 200, "N register V2 aboutit SANS aucun code OTP")
r = login("noopt@example.com", "motdepassesansotp")
check(r.status_code == 200, "O login V2 aboutit SANS aucun code OTP")

# --- P. endpoints OTP legacy toujours déclarés (compat anciennes apps) --
_rules = {r.rule for r in A.app.url_map.iter_rules()}
check("/api/auth/request-code" in _rules and "/api/auth/verify-code" in _rules,
      "P endpoints OTP legacy /api/auth/request-code & /verify-code toujours présents")
check("/api/app/auth/register" in _rules and "/api/app/auth/login" in _rules,
      "P nouveaux endpoints V2 /api/app/auth/register & /login enregistrés")

# --- Sécurité : le module ne logge jamais le mot de passe --------------
_src_auth = _SRC[_SRC.find("AUTH V2 — email + mot de passe"):
                 _SRC.find("def send_email_code")]
check(_re.search(r"print\([^)]*\bpassword\b", _src_auth, _re.I) is None,
      "SEC-1 aucun print() du bloc auth V2 (helpers) ne mentionne 'password'")
_src_ep = _SRC[_SRC.find("AUTH V2 — endpoints APP"):_SRC.find('@app.route("/api/auth/logout"')]
check(_re.search(r"(print|log(ger|ging)?\.)\s*\([^)]*\b(password|pw_hash|password_hash)\b",
                 _src_ep, _re.I) is None,
      "SEC-2 endpoints register/login ne loggent ni mot de passe ni hash")

print("-" * 64)
print(f"RESULTAT : {_STATE['pass']} OK / {_STATE['fail']} KO")
sys.exit(1 if _STATE["fail"] else 0)
