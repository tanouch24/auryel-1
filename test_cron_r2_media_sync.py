"""
test_cron_r2_media_sync.py — POST /cron/r2-media-sync + GET
/admin/content/r2-sync-status.

Vérifie l'auth constant-time (secret body OU en-tête X-Cron-Secret), le
comportement « R2 non configuré -> disabled », le contrat de réponse (compteurs
seulement, AUCUN secret), et l'endpoint admin de statut. La logique métier est
couverte par test_r2_media_sync.py. 100 % local : psycopg2 mocké, sync mocké.
"""
import sys
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

import os
for _k, _v in {
    "SECRET_KEY": "t", "VERIFY_TOKEN": "t", "ADMIN_PASSWORD": "t",
    "DATABASE_URL": "postgresql://t:t@localhost/t",
    "STRIPE_SK": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "WHATSAPP_TOKEN": "t", "PHONE_NUMBER_ID": "t", "GROQ_API_KEY": "t",
    "RESEND_API_KEY": "t", "META_APP_SECRET": "t",
    "DAILY_SECRET": "cron-secret-xyz", "SEO_SECRET": "t",
}.items():
    os.environ.setdefault(_k, _v)

import auryel_bot as A
import r2_media_sync as RS

_STATE = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _STATE["pass"] += 1
        print(f"OK  {label}")
    else:
        _STATE["fail"] += 1
        print(f"XX  {label}")


A.app.config["TESTING"] = True
try:
    A.limiter.enabled = False
except Exception:
    pass
client = A.app.test_client()

SECRET = "cron-secret-xyz"   # == DAILY_SECRET (repli de R2_SYNC_CRON_SECRET)


class _Cfg:
    def __init__(self, configured):
        self._c = configured

    @property
    def configured(self):
        return self._c

    def masked(self):
        # JAMAIS de secret : clé tronquée, secret masqué
        return {"account_id": "acc123…", "bucket": "auryel-meditations",
                "public_base_url": "https://pub-xxx.r2.dev",
                "access_key_id": "AKIA…", "secret_access_key": "***",
                "configured": self._c}


_FAKE_RESULT = {
    "status": "success", "audio_objects": 50, "video_objects": 12,
    "new_audio": 1, "new_videos": 0, "adopted_audio": 0, "adopted_videos": 0,
    "updated_audio": 49, "updated_videos": 12, "invalid_objects": 0,
    "missing_objects": 0, "errors": 0,
}


class _FakeSync:
    last_dry_run = None

    def __init__(self, *a, **k):
        pass

    def run(self, dry_run=False):
        _FakeSync.last_dry_run = dry_run
        return dict(_FAKE_RESULT)


def _install(configured=True):
    RS.R2Config.from_env = staticmethod(lambda env=None: _Cfg(configured))
    RS.R2Client = lambda cfg: object()
    RS.R2MediaCatalogSync = _FakeSync
    RS.read_sync_state = lambda gc: {
        "last_run_at": "2026-09-10T12:00:00+00:00", "status": "success",
        "audio_objects": 50, "video_objects": 12, "new_audio": 1,
        "new_videos": 0, "adopted_audio": 0, "adopted_videos": 0,
        "updated_audio": 49, "updated_videos": 12, "missing_objects": 0,
        "invalid_objects": 0, "errors": 0, "detail": None,
    }


# ---------------------------------------------------------------------------
_rules = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
check(("/cron/r2-media-sync", "POST") in _rules, "0a route cron enregistrée")
check(("/admin/content/r2-sync-status", "GET") in _rules,
      "0b route admin de statut enregistrée")

# --- auth ---
check(client.post("/cron/r2-media-sync").status_code == 401,
      "1a sans secret -> 401")
check(client.post("/cron/r2-media-sync",
                  json={"secret": "mauvais"}).status_code == 401,
      "1b mauvais secret -> 401")

# --- R2 non configuré -> disabled ---
_install(configured=False)
r = client.post("/cron/r2-media-sync", json={"secret": SECRET})
j = r.get_json()
check(r.status_code == 200 and j["status"] == "disabled"
      and j["reason"] == "r2_not_configured",
      "2a R2 non configuré -> 200 {status: disabled}")
check("secret_access_key" not in str(j) or j["config"]["secret_access_key"] == "***",
      "2b réponse disabled : aucun secret exposé")

# --- R2 configuré : secret dans l'en-tête ---
_install(configured=True)
r = client.post("/cron/r2-media-sync", headers={"X-Cron-Secret": SECRET})
j = r.get_json()
check(r.status_code == 200 and j["status"] == "success"
      and j["audio_objects"] == 50 and j["video_objects"] == 12
      and j["new_audio"] == 1,
      "3a secret en-tête OK -> 200 + compteurs")
check(set(j.keys()) == {
    "status", "dry_run", "audio_objects", "video_objects", "new_audio",
    "new_videos", "adopted_audio", "adopted_videos", "updated_audio",
    "updated_videos", "invalid_objects", "missing_objects", "errors"},
    "3b réponse = compteurs uniquement (aucune clé sensible, aucun credential)")
check("secret" not in "".join(k.lower() for k in j.keys()),
      "3c aucune clé « secret » dans la réponse")

# --- dry_run passe bien au sync ---
_FakeSync.last_dry_run = None
client.post("/cron/r2-media-sync", json={"secret": SECRET, "dry_run": True})
check(_FakeSync.last_dry_run is True, "4 body dry_run=true -> run(dry_run=True)")

# --- admin status : auth ---
with client.session_transaction() as s:
    s.clear()
check(client.get("/admin/content/r2-sync-status").status_code == 401,
      "5a status admin non connecté -> 401")

with client.session_transaction() as s:
    s["admin_logged"] = True
r = client.get("/admin/content/r2-sync-status")
j = r.get_json()
check(r.status_code == 200 and j["configured"] is True
      and j["state"]["status"] == "success"
      and j["state"]["audio_objects"] == 50,
      "5b status admin connecté -> 200 + état du dernier sync")
check(j["config"]["secret_access_key"] == "***"
      and j["config"]["access_key_id"].endswith("…")
      and "topsecret" not in str(j).lower(),
      "5c status admin : credentials R2 jamais renvoyés (masqués)")

# --- migration v46 : additive, aucune opération destructrice ---
import inspect
_src = inspect.getsource(A.init_db)
_i = _src.find("Migration v46")
check(_i != -1, "6a bloc « Migration v46 » présent dans init_db()")
_seg = "\n".join(
    ln for ln in _src[_i:_i + 4000].splitlines()
    if not ln.lstrip().startswith("#")
)
check("ADD COLUMN IF NOT EXISTS r2_object_key TEXT" in _seg
      and "ADD COLUMN IF NOT EXISTS r2_last_seen_at" in _seg,
      "6b ajoute r2_object_key + r2_last_seen_at (ADD COLUMN IF NOT EXISTS)")
check("CREATE UNIQUE INDEX IF NOT EXISTS" in _seg
      and "WHERE r2_object_key IS NOT NULL" in _seg,
      "6c index UNIQUE PARTIEL sur r2_object_key (garantie DB anti-doublon)")
check("CREATE TABLE IF NOT EXISTS r2_sync_state" in _seg,
      "6d table r2_sync_state (IF NOT EXISTS)")
for _forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "DROP COLUMN"):
    check(_forbidden not in _seg, f"6e aucune opération destructrice : {_forbidden!r}")
check("REFERENCES accounts" not in _seg,
      "6f aucune FK vers accounts (contenu global)")

print("-" * 60)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
