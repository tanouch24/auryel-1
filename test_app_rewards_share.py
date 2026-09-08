"""
test_app_rewards_share.py — J5 : récompense « 30 jours de partage = 1 h ».

Routes testées :
    POST /api/app/rewards/daily-share
    GET  /api/app/rewards/share-progress

100 % local : psycopg2 mocké, get_conn -> fausse DB en mémoire modélisant
`share_reward_days` (unicité (user_id, share_date) réellement appliquée, comme
la PRIMARY KEY en base) + `accounts` (purchased_seconds_remaining /
share_reward_credited_at). resolve_app_session monkeypatché. Aucun réseau,
aucune DB réelle, aucun LLM. Style aligné sur test_app_tirage.py.
"""

import sys
from datetime import date, timedelta
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


UID1 = "11111111-1111-4111-8111-111111111111"
UID2 = "22222222-2222-4222-8222-222222222222"

# ---------------------------------------------------------------------------
# Fausse DB : share_reward_days (set (uid, isodate)) + accounts.
# ---------------------------------------------------------------------------
_DAYS = set()                       # {(uid, "YYYY-MM-DD")}
_ACCOUNTS = {}                      # uid -> {purchased, credited_at, deleted_at}


def _reset_db():
    _DAYS.clear()
    _ACCOUNTS.clear()
    _ACCOUNTS[UID1] = {"purchased": 0, "credited_at": None, "deleted_at": None}
    _ACCOUNTS[UID2] = {"purchased": 0, "credited_at": None, "deleted_at": None}


class _Cur:
    def __init__(self):
        self._r = None
        self.rowcount = -1

    def fetchone(self):
        return self._r

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = " ".join(sql.split())
        p = params
        self._r = None
        self.rowcount = -1

        if k in (
            "SELECT user_id FROM accounts WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE",
            "SELECT user_id FROM accounts WHERE user_id=%s AND deleted_at IS NULL",
        ):
            uid = p[0]
            acc = _ACCOUNTS.get(uid)
            self._r = (uid,) if (acc and acc["deleted_at"] is None) else None

        elif k == ("INSERT INTO share_reward_days (user_id, share_date, created_at) "
                   "VALUES (%s, %s, %s) ON CONFLICT (user_id, share_date) DO NOTHING"):
            uid, sdate, _created = p
            key = (uid, str(sdate))
            if key in _DAYS:
                self.rowcount = 0          # ON CONFLICT DO NOTHING
            else:
                _DAYS.add(key)
                self.rowcount = 1

        elif k == "SELECT COUNT(*) FROM share_reward_days WHERE user_id=%s":
            uid = p[0]
            self._r = (sum(1 for (u, _d) in _DAYS if u == uid),)

        elif k == ("UPDATE accounts SET purchased_seconds_remaining = "
                   "COALESCE(purchased_seconds_remaining, 0) + %s, "
                   "share_reward_credited_at = %s "
                   "WHERE user_id=%s AND share_reward_credited_at IS NULL"):
            secs, ts, uid = p
            acc = _ACCOUNTS.get(uid)
            if acc and acc["credited_at"] is None:
                acc["purchased"] += secs
                acc["credited_at"] = ts
                self.rowcount = 1
            else:
                self.rowcount = 0

        else:
            raise AssertionError("SQL non géré par le fake rewards : " + k)


class _Conn:
    def cursor(self):
        return _Cur()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


A.get_conn = lambda: _Conn()
A.app.config["TESTING"] = True
try:
    A.limiter.enabled = False
except Exception:
    pass
client = A.app.test_client()

_CURRENT = {"uid": UID1}
A.resolve_app_session = lambda tok: (
    {"session_id": "s", "user_id": _CURRENT["uid"], "email": "x@x.co"} if tok else None
)

_FAKE_TODAY = {"d": date(2026, 9, 1)}
A._reward_share_date = lambda now=None: _FAKE_TODAY["d"]


def _as(uid):
    _CURRENT["uid"] = uid


def _hdr():
    return {"Authorization": "Bearer x"}


def _post():
    return client.post("/api/app/rewards/daily-share", headers=_hdr())


def _get():
    return client.get("/api/app/rewards/share-progress", headers=_hdr())


# ===========================================================================
# 0. Routes existent aux chemins EXACTS attendus par Flutter
# ===========================================================================
_rules = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
check(("/api/app/rewards/daily-share", "POST") in _rules,
      "0a POST /api/app/rewards/daily-share enregistrée")
check(("/api/app/rewards/share-progress", "GET") in _rules,
      "0b GET /api/app/rewards/share-progress enregistrée")

# ===========================================================================
# 13. GET initial = 0/30
# ===========================================================================
_reset_db()
_as(UID1)
j = _get().get_json()
check(j == {"count": 0, "target": 30, "credited": False, "credited_seconds": 0},
      "13 GET initial -> 0/30, credited false")

# ===========================================================================
# 14. premier POST = 1/30, aucun crédit
# ===========================================================================
_reset_db()
_as(UID1)
_FAKE_TODAY["d"] = date(2026, 9, 1)
j = _post().get_json()
check(j["count"] == 1 and j["target"] == 30, "14a premier POST -> 1/30")
check(j["credited"] is False and j["credited_seconds"] == 0, "14b aucun crédit au jour 1")
check(set(j.keys()) == {"count", "target", "credited", "credited_seconds"},
      "14c ShareProgress : exactement count/target/credited/credited_seconds")

# ===========================================================================
# 15. deuxième POST le MÊME jour = toujours 1/30
# ===========================================================================
j2 = _post().get_json()
check(j2["count"] == 1, "15a même jour rappelé -> toujours 1/30")
check(j2["credited"] is False and j2["credited_seconds"] == 0, "15b même jour -> aucun crédit")
check(sum(1 for (u, _d) in _DAYS if u == UID1) == 1, "15c 1 seul jour en base")

# ===========================================================================
# 16. 29 jours distincts -> aucun crédit
# ===========================================================================
_reset_db()
_as(UID1)
last = None
for i in range(29):
    _FAKE_TODAY["d"] = date(2026, 9, 1) + timedelta(days=i)
    last = _post().get_json()
check(last["count"] == 29, "16a 29 jours distincts -> 29/30")
check(last["credited"] is False and last["credited_seconds"] == 0, "16b 29 jours -> aucun crédit")
check(_ACCOUNTS[UID1]["purchased"] == 0, "16c portefeuille inchangé à 29 jours")

# ===========================================================================
# 17. 30e jour -> credited=true + credited_seconds=3600
# ===========================================================================
_FAKE_TODAY["d"] = date(2026, 9, 30)
j30 = _post().get_json()
check(j30["count"] == 30 and j30["target"] == 30, "17a 30e jour -> 30/30")
check(j30["credited"] is True, "17b 30e jour -> credited=true")
check(j30["credited_seconds"] == 3600, "17c 30e jour -> credited_seconds=3600")

# ===========================================================================
# 18. portefeuille +3600 EXACTEMENT (bucket purchased_seconds_remaining)
# ===========================================================================
check(_ACCOUNTS[UID1]["purchased"] == 3600, "18a purchased_seconds_remaining = +3600")
check(_ACCOUNTS[UID1]["credited_at"] is not None, "18b share_reward_credited_at posé")

# ===========================================================================
# 19. retry du 30e jour -> credited=false + 0 sec, portefeuille inchangé
# ===========================================================================
jr = _post().get_json()
check(jr["count"] == 30, "19a retry -> toujours 30/30")
check(jr["credited"] is False and jr["credited_seconds"] == 0, "19b retry -> aucun 2e crédit")
check(_ACCOUNTS[UID1]["purchased"] == 3600, "19c portefeuille toujours +3600 (pas +7200)")

# ===========================================================================
# 20. GET après crédit -> 30/30, aucun nouveau crédit
# ===========================================================================
before = _ACCOUNTS[UID1]["purchased"]
jg = _get().get_json()
check(jg["count"] == 30 and jg["target"] == 30, "20a GET après crédit -> 30/30")
check(jg["credited"] is False and jg["credited_seconds"] == 0, "20b GET n'accorde jamais de crédit")
check(_ACCOUNTS[UID1]["purchased"] == before, "20c GET ne touche pas le portefeuille")

# ===========================================================================
# 21. idempotence garantie EN BASE (contrainte d'unicité)
# ===========================================================================
_reset_db()
_as(UID1)
_FAKE_TODAY["d"] = date(2026, 10, 5)
for _ in range(6):
    _post()
check(sum(1 for (u, _d) in _DAYS if u == UID1) == 1,
      "21a 6 POST le même jour -> 1 seul jour comptabilisé")
_src = " ".join(open("auryel_bot.py").read().split())
check("ON CONFLICT (user_id, share_date) DO NOTHING" in _src,
      "21b la route utilise ON CONFLICT (user_id, share_date) DO NOTHING")
check("WHERE user_id=%s AND share_reward_credited_at IS NULL" in _src,
      "21c le crédit est gardé par WHERE share_reward_credited_at IS NULL")
_mig = " ".join(open("migrations/011_share_reward.sql").read().split())
check("PRIMARY KEY (user_id, share_date)" in _mig,
      "21d migration : PRIMARY KEY (user_id, share_date) (unicité en base)")

# ===========================================================================
# 22. deuxième récompense impossible (au-delà de 30 jours -> aucun 2e crédit)
# ===========================================================================
_reset_db()
_as(UID1)
for i in range(31):
    _FAKE_TODAY["d"] = date(2026, 9, 1) + timedelta(days=i)
    jj = _post().get_json()
check(jj["count"] == 30, "22a 31 jours distincts -> count plafonné à 30")
check(jj["target"] == 30, "22b target reste 30 (aucun nouveau cycle)")
check(_ACCOUNTS[UID1]["purchased"] == 3600, "22c un seul crédit total (+3600, jamais +7200)")

# ===========================================================================
# 23. utilisateurs isolés
# ===========================================================================
_reset_db()
_as(UID1)
for i in range(30):
    _FAKE_TODAY["d"] = date(2026, 9, 1) + timedelta(days=i)
    _post()
check(_ACCOUNTS[UID1]["purchased"] == 3600, "23a UID1 crédité")
_as(UID2)
jj = _get().get_json()
check(jj["count"] == 0, "23b UID2 -> 0/30 (indépendant de UID1)")
check(_ACCOUNTS[UID2]["purchased"] == 0 and _ACCOUNTS[UID2]["credited_at"] is None,
      "23c UID2 portefeuille et marqueur intacts")
_FAKE_TODAY["d"] = date(2026, 9, 1)
jj = _post().get_json()
check(jj["count"] == 1 and jj["credited"] is False, "23d UID2 démarre son propre compteur à 1")

# ===========================================================================
# 24. sans auth -> refus
# ===========================================================================
check(client.post("/api/app/rewards/daily-share").status_code == 401,
      "24a POST sans Bearer -> 401")
check(client.get("/api/app/rewards/share-progress").status_code == 401,
      "24b GET sans Bearer -> 401")

# compte supprimé -> 401 même avec un jeton
_reset_db()
_ACCOUNTS[UID1]["deleted_at"] = A._utcnow()
_as(UID1)
check(_post().status_code == 401, "24c POST sur compte supprimé -> 401")
check(_get().status_code == 401, "24d GET sur compte supprimé -> 401")

# ===========================================================================
print("-" * 64)
print(f"{_STATE['pass']}/{_STATE['pass'] + _STATE['fail']} tests passés")
sys.exit(1 if _STATE["fail"] else 0)
