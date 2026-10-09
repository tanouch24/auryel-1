"""Roue cadeau (09/10/2026) — POST /api/app/rewards/gift-wheel.

+3 min une seule fois par compte, quand le temps gratuit est presque épuisé ;
jamais pour un Premium, jamais si le garde-fou détresse bloque le marketing.
100 % local : DB, temps et auth simulés.
"""
import os
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("psycopg2", MagicMock())
for _k, _v in {
    "SECRET_KEY": "t", "ADMIN_PASSWORD": "t",
    "DATABASE_URL": "postgresql://t:t@127.0.0.1:1/t",
}.items():
    os.environ.setdefault(_k, _v)

import pytest  # noqa: E402

import auryel_bot as A  # noqa: E402

UID = "11111111-1111-4111-8111-111111111111"


class _Cur:
    def __init__(self, db):
        self.db = db
        self._r = None
        self.rowcount = -1

    def fetchone(self):
        return self._r

    def execute(self, sql, params=()):
        k = " ".join(sql.split())
        self._r, self.rowcount = None, -1
        acc = self.db["accounts"].get(params[-1] if params else None)
        if k.startswith("SELECT user_id FROM accounts WHERE user_id=%s AND deleted_at IS NULL"):
            self._r = (params[0],) if acc else None
        elif k == "SELECT gift_wheel_credited_at FROM accounts WHERE user_id=%s":
            self._r = (acc["gift_at"],) if acc else None
        elif k.startswith("UPDATE accounts SET gift_wheel_credited_at=%s"):
            if acc and acc["gift_at"] is None:
                acc["gift_at"] = params[0]
                self.rowcount = 1
            else:
                self.rowcount = 0
        else:
            raise AssertionError("SQL non géré : " + k)


class _Conn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return _Cur(self.db)

    def commit(self):
        self.db["commits"] += 1

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def env(monkeypatch):
    db = {"accounts": {UID: {"gift_at": None, "earned": 0}}, "commits": 0,
          "premium": False, "remaining": 0, "distress": False}

    def credit(cursor, user_id, seconds, reason, key, now):
        db["accounts"][user_id]["earned"] += seconds
        return {"credited": True, "earned_remaining_seconds": seconds}

    monkeypatch.setattr(A, "get_conn", lambda: _Conn(db))
    monkeypatch.setattr(A, "resolve_app_session",
                        lambda tok: {"session_id": "s", "user_id": UID, "email": "x@x.co"} if tok else None)
    monkeypatch.setattr(A, "_explorer_premium_tx", lambda c, u, n: db["premium"])
    monkeypatch.setattr(A, "_get_time_snapshot_tx",
                        lambda c, u, n: {"total_remaining_seconds": db["remaining"]})
    monkeypatch.setattr(A, "_credit_bonus_time_tx", credit)
    monkeypatch.setattr(A, "_gift_wheel_distress_blocked", lambda u, n: db["distress"])
    monkeypatch.setattr(A, "_state_with_time_settle", lambda u, now=None: {"st": 1})
    monkeypatch.setattr(A, "_time_json", lambda st: {"total_remaining_seconds": 180})
    monkeypatch.setattr(A, "_quota_shim_json", lambda st: {"is_premium": False})
    A.app.config["TESTING"] = True
    A.limiter.enabled = False
    return db, A.app.test_client()


def _post(client, body=None):
    r = client.post("/api/app/rewards/gift-wheel", json=body or {},
                    headers={"Authorization": "Bearer x"})
    return r.status_code, r.get_json()


def test_check_says_eligible_without_crediting(env):
    db, client = env
    code, j = _post(client, {"check": True})
    assert code == 200 and j["eligible"] is True and j["credited"] is False
    assert db["accounts"][UID]["earned"] == 0 and db["accounts"][UID]["gift_at"] is None


def test_spin_credits_180_seconds_once(env):
    db, client = env
    code, j = _post(client)
    assert code == 200 and j["credited"] is True and j["credited_seconds"] == 180
    assert j["time"] == {"total_remaining_seconds": 180}
    assert db["accounts"][UID]["earned"] == 180
    code, j = _post(client)
    assert j["credited"] is False and j["reason"] == "already_used"
    assert db["accounts"][UID]["earned"] == 180


@pytest.mark.parametrize("field,value,reason", [
    ("premium", True, "premium"),
    ("remaining", 600, "time_left"),
    ("distress", True, "not_eligible"),
])
def test_never_for_premium_time_left_or_distress(env, field, value, reason):
    db, client = env
    db[field] = value
    for body in ({"check": True}, {}):
        code, j = _post(client, body)
        assert code == 200 and j["eligible"] is False and j["credited"] is False
        assert j["reason"] == reason
    assert db["accounts"][UID]["earned"] == 0


def test_amount_is_never_chosen_by_the_client(env):
    db, client = env
    _post(client, {"seconds": 99999})
    assert db["accounts"][UID]["earned"] == 180


def test_requires_auth(env):
    _, client = env
    r = client.post("/api/app/rewards/gift-wheel", json={})
    assert r.status_code == 401


def test_distress_guard_fails_safe(monkeypatch):
    def boom(_):
        raise RuntimeError("db down")
    monkeypatch.setattr(A, "get_app_profile", boom)
    assert A._gift_wheel_distress_blocked(UID, A._utcnow()) is True


def test_migration_is_wired():
    sql = open(os.path.join(os.path.dirname(__file__), "migrations", "053_gift_wheel.sql")).read()
    assert "ADD COLUMN IF NOT EXISTS gift_wheel_credited_at TIMESTAMPTZ" in sql
    import inspect
    assert "053_gift_wheel.sql" in inspect.getsource(A.init_db)
