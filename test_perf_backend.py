"""
test_perf_backend.py — Partie E : service threadé + pool PostgreSQL borné +
/health réel + délais LLM bornés + APScheduler retiré du process web.

100 % local : psycopg2 mocké, faux pool en mémoire, /health monkeypatché sur
_db_ping. Aucun serveur lancé, aucun réseau.
"""

import sys
import json
import inspect
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


IDLE = A.psycopg2.extensions.TRANSACTION_STATUS_IDLE   # même singleton que le code


# ---------------------------------------------------------------------------
# Faux pool + fausse connexion brute
# ---------------------------------------------------------------------------
class FakeRawConn:
    def __init__(self, tx_status=None, broken=False):
        self._tx = tx_status if tx_status is not None else IDLE
        self.broken = broken
        self.rolled_back = 0
        self.committed = 0
        self.physically_closed = 0

    def get_transaction_status(self):
        if self.broken:
            raise RuntimeError("connexion cassée")
        return self._tx

    def rollback(self):
        self.rolled_back += 1
        self._tx = IDLE

    def commit(self):
        self.committed += 1

    def close(self):
        self.physically_closed += 1

    def cursor(self):
        return MagicMock()


class FakePool:
    def __init__(self):
        self.returned = []          # (conn, close_flag)
        self.handed_out = []

    def getconn(self):
        c = FakeRawConn()
        self.handed_out.append(c)
        return c

    def putconn(self, conn, close=False):
        self.returned.append((conn, close))


# ===========================================================================
# 1. _PooledConn.close() RESTITUE au pool (jamais de close physique)
# ===========================================================================
pool = FakePool()
raw = FakeRawConn(tx_status=IDLE)
pc = A._PooledConn(raw, pool)
pc.close()
check(pool.returned == [(raw, False)] and raw.physically_closed == 0,
      "1a close() sur connexion IDLE -> putconn(conn), aucun close physique")
# double close = no-op
pc.close()
check(len(pool.returned) == 1, "1b close() idempotent (pas de double restitution)")

# ===========================================================================
# 2. Transaction restée ouverte -> rollback AVANT restitution
# ===========================================================================
pool = FakePool()
raw = FakeRawConn(tx_status="INTRANS")
pc = A._PooledConn(raw, pool)
pc.close()
check(raw.rolled_back == 1 and pool.returned == [(raw, False)],
      "2 tx ouverte -> rollback puis putconn ; jamais rendue avec une tx ouverte")

# ===========================================================================
# 3. Connexion cassée -> putconn(close=True), retirée du pool
# ===========================================================================
pool = FakePool()
raw = FakeRawConn(broken=True)
pc = A._PooledConn(raw, pool)
pc.close()
check(pool.returned == [(raw, True)],
      "3 connexion cassée -> putconn(conn, close=True) (retirée du pool)")

# ===========================================================================
# 4. `with get_conn() as conn:` = commit/rollback, PAS close
# ===========================================================================
pool = FakePool()
raw = FakeRawConn()
pc = A._PooledConn(raw, pool)
with pc as c:
    check(c is pc, "4a __enter__ renvoie le proxy")
check(raw.committed == 1 and pool.returned == [],
      "4b sortie normale -> commit, AUCUNE restitution (le finally: close() de l'appelant s'en charge)")

pool = FakePool()
raw = FakeRawConn()
pc = A._PooledConn(raw, pool)
try:
    with pc:
        raise ValueError("boom")
except ValueError:
    pass
check(raw.rolled_back == 1 and raw.committed == 0,
      "4c exception dans le with -> rollback, pas de commit")

# ===========================================================================
# 5. get_conn() : repli sur connexion directe si le pool échoue
# ===========================================================================
_orig_pool = A._get_db_pool
A._get_db_pool = lambda: (_ for _ in ()).throw(RuntimeError("pool KO"))
A.psycopg2.connect.reset_mock()
c = A.get_conn()
check(A.psycopg2.connect.called, "5 pool indisponible -> get_conn() retombe sur psycopg2.connect direct")
A._get_db_pool = _orig_pool

# ===========================================================================
# 6. Proxy : délégation transparente des attributs inconnus
# ===========================================================================
raw = FakeRawConn()
raw.custom_attr = 42
pc = A._PooledConn(raw, FakePool())
check(pc.custom_attr == 42 and pc.get_transaction_status() == IDLE,
      "6 __getattr__ délègue à la vraie connexion")

# ===========================================================================
# 7. DB_POOL_MIN / DB_POOL_MAX depuis l'environnement
# ===========================================================================
check(isinstance(A.DB_POOL_MIN, int) and isinstance(A.DB_POOL_MAX, int)
      and A.DB_POOL_MIN >= 1 and A.DB_POOL_MAX >= A.DB_POOL_MIN,
      "7a DB_POOL_MIN/MAX bornés et cohérents (min>=1, max>=min)")
_src = inspect.getsource(A)
check('os.environ.get("DB_POOL_MIN"' in _src and 'os.environ.get("DB_POOL_MAX"' in _src,
      "7b configurables par DB_POOL_MIN / DB_POOL_MAX")

# ===========================================================================
# 8. /health RÉEL : 200 si DB up, 503 si DB down, aucune info sensible
# ===========================================================================
_ping_src = inspect.getsource(A._db_ping)   # capturé AVANT tout monkeypatch
check("SELECT 1" in _ping_src and "connect_timeout" in _ping_src,
      "8d _db_ping fait un vrai SELECT 1 avec délai court, connexion dédiée")

A.app.config["TESTING"] = True
try:
    A.limiter.enabled = False
except Exception:
    pass
client = A.app.test_client()

A._db_ping = lambda timeout_s=2: True
r = client.get("/health")
check(r.status_code == 200 and r.get_json() == {"status": "ok"},
      "8a DB disponible -> 200 {status: ok}")

A._db_ping = lambda timeout_s=2: False
r = client.get("/health")
body = r.get_data(as_text=True)
check(r.status_code == 503 and r.get_json() == {"status": "unavailable"},
      "8b DB indisponible -> 503 {status: unavailable}")
check("postgres" not in body.lower() and "localhost" not in body.lower()
      and "timestamp" not in body.lower() and "version" not in body.lower(),
      "8c aucune info sensible (DSN / hôte / version / horodatage) dans /health")

# ===========================================================================
# 9. APScheduler retiré du process web
# ===========================================================================
check(not hasattr(A, "scheduler") and not hasattr(A, "BackgroundScheduler"),
      "9a plus d'objet scheduler / BackgroundScheduler dans le module")
check("from apscheduler" not in _src and "scheduler.start()" not in _src
      and "BackgroundScheduler(" not in _src,
      "9b plus d'import ni de démarrage APScheduler dans auryel_bot.py")
_req = open(os.path.join(os.path.dirname(A.__file__), "requirements.txt")).read().lower()
check("apscheduler" not in _req, "9c apscheduler retiré de requirements.txt")

# ===========================================================================
# 10. Config gunicorn cible (railway.json)
# ===========================================================================
_rj = json.load(open(os.path.join(os.path.dirname(A.__file__), "railway.json")))
_cmd = _rj["deploy"]["startCommand"]
check("--worker-class gthread" in _cmd and "--threads 8" in _cmd
      and "--workers 1" in _cmd and "--timeout 120" in _cmd
      and "auryel_bot:app" in _cmd,
      "10 railway.json : gunicorn gthread / 1 worker / 8 threads / timeout 120")

# ===========================================================================
# 11. Délais LLM bornés (< 45 s) et configurables ; chaîne de repli bornée
# ===========================================================================
check(A.LLM_HTTP_TIMEOUT <= 30 and A.LLM_HTTP_TIMEOUT * 3 < 120,
      "11a LLM_HTTP_TIMEOUT réduit (<=30 s) -> pire cas 3 fournisseurs < gunicorn timeout")
_llm_src = inspect.getsource(A._call_llm_once)
check("timeout=45" not in _llm_src and _llm_src.count("timeout=LLM_HTTP_TIMEOUT") >= 3,
      "11b _call_llm_once : chaque fournisseur borné par LLM_HTTP_TIMEOUT (openai/openrouter/groq)")
check('chain = [provider]' in _llm_src
      and _llm_src.count('chain.append(') == 2
      and _llm_src.count("for p in chain") == 1,
      "11c chaîne de repli bornée et fixe (provider -> openrouter -> groq)")
_wrap_src = inspect.getsource(A.call_llm)
check("_call_llm_once(" in _wrap_src and "fallback_failure" in _wrap_src
      and "_llm_output_safety_filter(" in _wrap_src,
      "11d call_llm : signal fallback_failure + filtre de sortie borné")

# ---------------------------------------------------------------------------
print("-" * 60)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
