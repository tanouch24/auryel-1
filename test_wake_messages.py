"""
test_wake_messages.py — Réveil Auryel, messages du matin (Migration v47) :
endpoint mobile lecture seule + seed idempotent + extensibilité.

100 % local : psycopg2 mocké, fausse DB en mémoire modélisant
`wake_messages`. Aucun réseau, aucun LLM. Couvre :
  - GET /api/app/content/wake-messages : auth, actifs seulement, contrat
    versionné, ETag / If-None-Match -> 304, catalogue vide -> 200
    messages:[], audio_url null accepté (repli TTS local côté app).
  - seed initial : 60 phrases, idempotent (ON CONFLICT (text) DO NOTHING).
  - extensibilité : 60 -> 61 -> 120 messages sans changement Flutter.
  - aucun endpoint d'écriture non protégé sur /api/app/content/wake-messages.
  - migration v47 additive (inspection de source) : aucun DROP / TRUNCATE /
    DELETE / ALTER destructeur, aucune FK vers accounts.
"""

import sys
import inspect
from datetime import datetime, timezone
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

import os
for _k, _v in {
    "SECRET_KEY": "test-secret-key", "VERIFY_TOKEN": "test", "ADMIN_PASSWORD": "test",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "STRIPE_SK": "sk_test_placeholder", "STRIPE_WEBHOOK_SECRET": "whsec_placeholder",
    "WHATSAPP_TOKEN": "test", "PHONE_NUMBER_ID": "test", "GROQ_API_KEY": "test",
    "RESEND_API_KEY": "test", "META_APP_SECRET": "test", "DAILY_SECRET": "test",
    "SEO_SECRET": "test",
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
MSGS = []
_SEQ = [0]


def _reset():
    MSGS.clear()
    _SEQ[0] = 0


def _norm(s):
    return " ".join(s.split())


class Cur:
    def __init__(self):
        self._one = None
        self._rows = None
        self.rowcount = -1

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows or []

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = _norm(sql)
        p = tuple(params or ())
        self._one = None
        self._rows = None
        self.rowcount = -1

        if k.startswith("SELECT id, text, audio_url, updated_at FROM "
                         "wake_messages WHERE is_active = TRUE"):
            rows = [m for m in MSGS if m["is_active"]]
            rows.sort(key=lambda m: m["id"])
            self._rows = [(m["id"], m["text"], m["audio_url"], m["updated_at"])
                          for m in rows]

        elif k.startswith("INSERT INTO wake_messages (id, text) VALUES "
                           "(%s, %s) ON CONFLICT (text) DO NOTHING"):
            vid, text = p
            existing = next((m for m in MSGS if m["text"] == text), None)
            if not existing:
                MSGS.append({
                    "id": vid, "text": text, "is_active": True,
                    "audio_url": None,
                    "updated_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
                })
            self.rowcount = 0 if existing else 1

        else:
            raise AssertionError("SQL non géré par le fake réveil : " + k)


class Conn:
    def cursor(self):
        return Cur()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


A.get_conn = lambda: Conn()
A.app.config["TESTING"] = True
try:
    A.limiter.enabled = False
except Exception:
    pass
client = A.app.test_client()

A.resolve_app_session = lambda tok: (
    {"session_id": "s", "user_id": UID, "email": "u@auryel.co"} if tok else None
)
H = {"Authorization": "Bearer x"}


def _seed_msg(text, is_active=True, audio_url=None):
    _SEQ[0] += 1
    n = _SEQ[0]
    MSGS.append({
        "id": f"{n:08d}-0000-4000-8000-000000000000",
        "text": text, "is_active": is_active, "audio_url": audio_url,
        "updated_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
    })


# ===========================================================================
# 1. GET /api/app/content/wake-messages — auth + contrat
# ===========================================================================
_reset()
check(client.get("/api/app/content/wake-messages").status_code == 401,
      "1a sans Bearer -> 401")

_seed_msg("Message actif un")
_seed_msg("Message inactif", is_active=False)
r = client.get("/api/app/content/wake-messages", headers=H)
j = r.get_json()
check(r.status_code == 200, "1b 200 avec Bearer")
check([m["text"] for m in j["messages"]] == ["Message actif un"],
      "1c seuls les messages actifs sont servis")
check(j["version"] == A._CONTENT_API_VERSION and isinstance(j["catalog_version"], str),
      "1d version + catalog_version présents")
m0 = j["messages"][0]
check(set(m0) == {"id", "text", "audio_url"}, "1e contrat minimal : id/text/audio_url")
check(m0["audio_url"] is None, "1f audio_url absent -> null (repli TTS local côté app)")
check(r.headers.get("ETag") == f'"{j["catalog_version"]}"',
      "1g en-tête ETag = catalog_version")

# ===========================================================================
# 2. audio_url renseigné (MP3 R2 généré) -> transmis tel quel
# ===========================================================================
_reset()
_seed_msg("Message avec audio",
          audio_url="https://media.auryel.app/wake-messages/01.mp3")
j = client.get("/api/app/content/wake-messages", headers=H).get_json()
check(j["messages"][0]["audio_url"]
      == "https://media.auryel.app/wake-messages/01.mp3",
      "2a audio_url R2 transmis sans modification quand disponible")

# ===========================================================================
# 3. ETag / If-None-Match -> 304
# ===========================================================================
_reset()
_seed_msg("Un message")
j = client.get("/api/app/content/wake-messages", headers=H).get_json()
tag = j["catalog_version"]
r304 = client.get("/api/app/content/wake-messages",
                   headers={**H, "If-None-Match": f'"{tag}"'})
check(r304.status_code == 304 and not (r304.data or b"").strip(),
      "3a If-None-Match == catalog_version -> 304, pas de retéléchargement")
check(r304.headers.get("ETag") == f'"{tag}"', "3b 304 renvoie quand même l'ETag")
r200 = client.get("/api/app/content/wake-messages",
                   headers={**H, "If-None-Match": '"obsolete"'})
check(r200.status_code == 200, "3c ETag périmé -> 200 complet")

# ===========================================================================
# 4. Table VIDE — le système fonctionne sans aucun message
# ===========================================================================
_reset()
r = client.get("/api/app/content/wake-messages", headers=H)
j = r.get_json()
check(r.status_code == 200 and j["messages"] == [],
      "4a table vide -> 200 messages:[] (jamais bloquant)")

# ===========================================================================
# 5. Seed initial — 60 phrases, aucun doublon, idempotent
# ===========================================================================
check(len(A._WAKE_MESSAGES_SEED) == 60, "5a exactement 60 phrases dans le seed")
check(len(set(A._WAKE_MESSAGES_SEED)) == 60, "5b aucun doublon dans le seed")
check(all(isinstance(p, str) and p.strip() for p in A._WAKE_MESSAGES_SEED),
      "5c toutes les phrases sont des chaînes non vides")
check(not any("{" in p or "}" in p for p in A._WAKE_MESSAGES_SEED),
      "5d aucun gabarit de prénom (pas d'accolades type {prenom})")

_reset()
conn = A.get_conn()
c = conn.cursor()
inserted = 0
for phrase in A._WAKE_MESSAGES_SEED:
    import uuid as _uuid
    c.execute(
        "INSERT INTO wake_messages (id, text) VALUES (%s, %s) "
        "ON CONFLICT (text) DO NOTHING",
        (str(_uuid.uuid4()), phrase),
    )
    inserted += c.rowcount
check(inserted == 60, "5f premier seed : 60 lignes réellement insérées")
check(len(MSGS) == 60, "5g 60 messages en base après le 1er seed")

# rejouer le seed : idempotent, aucun doublon
inserted2 = 0
for phrase in A._WAKE_MESSAGES_SEED:
    import uuid as _uuid
    c.execute(
        "INSERT INTO wake_messages (id, text) VALUES (%s, %s) "
        "ON CONFLICT (text) DO NOTHING",
        (str(_uuid.uuid4()), phrase),
    )
    inserted2 += c.rowcount
check(inserted2 == 0, "5h rejouer le seed n'insère RIEN de plus (idempotent)")
check(len(MSGS) == 60, "5i toujours 60 messages après un rejeu du seed")

# ===========================================================================
# 6. EXTENSIBILITÉ — 60 -> 61 -> 120 messages, sans changement Flutter
# ===========================================================================
j60 = client.get("/api/app/content/wake-messages", headers=H).get_json()
tag60 = j60["catalog_version"]
check(len(j60["messages"]) == 60, "6a 60 messages servis")
_seed_msg("Un 61e message ajouté après coup")
j61 = client.get("/api/app/content/wake-messages", headers=H).get_json()
check(len(j61["messages"]) == 61 and j61["catalog_version"] != tag60,
      "6b 61e message présent + ETag changé, sans nouvelle version app")
for i in range(62, 121):
    _seed_msg(f"Message additionnel numero {i}")
j120 = client.get("/api/app/content/wake-messages", headers=H).get_json()
check(len(j120["messages"]) == 120, "6c 120 messages servis — aucun plafond codé")

# ===========================================================================
# 7. Aucun endpoint d'écriture non protégé pour wake_messages côté mobile
# ===========================================================================
for method in ("POST", "PUT", "PATCH", "DELETE"):
    r = client.open("/api/app/content/wake-messages", method=method, headers=H)
    check(r.status_code in (404, 405),
          f"7a {method} /api/app/content/wake-messages -> {r.status_code} "
          "(pas d'écriture mobile)")

# ===========================================================================
# 8. Migration v47 — additive, aucun DROP / ALTER destructeur
# ===========================================================================
src = inspect.getsource(A.init_db)
i47 = src.find("Migration v47")
check(i47 != -1, "8a bloc 'Migration v47' présent dans init_db()")
seg_full = src[i47:i47 + 3000]
seg_code = "\n".join(
    ln for ln in seg_full.splitlines() if not ln.lstrip().startswith("#")
)
check("CREATE TABLE IF NOT EXISTS wake_messages" in seg_code,
      "8b crée wake_messages (IF NOT EXISTS)")
check("idx_wake_messages_active" in seg_code, "8c index actif créé")
check("ON CONFLICT (text) DO NOTHING" in seg_code,
      "8d seed idempotent (ON CONFLICT DO NOTHING)")
for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "ALTER TABLE accounts"):
    check(forbidden not in seg_code,
          f"8e aucune opération destructrice : {forbidden!r}")
check("REFERENCES accounts" not in seg_code,
      "8f aucune FK vers accounts (contenu global)")

# ===========================================================================
print("-" * 68)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
