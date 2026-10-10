"""Roue « question offerte » (09/10/2026) — POST /api/app/rewards/question-wheel."""
import os
import sys
from datetime import timedelta
from unittest.mock import MagicMock

sys.modules.setdefault("psycopg2", MagicMock())
for _k, _v in {"SECRET_KEY": "t", "ADMIN_PASSWORD": "t",
               "DATABASE_URL": "postgresql://t:t@127.0.0.1:1/t"}.items():
    os.environ.setdefault(_k, _v)

import pytest  # noqa: E402

import auryel_bot as A  # noqa: E402

UID = "11111111-1111-4111-8111-111111111111"


class _Cur:
    def __init__(self, db):
        self.db = db
        self._r = None

    def fetchone(self):
        return self._r

    def execute(self, sql, params=()):
        k = " ".join(sql.split())
        self._r = None
        if k.startswith("SELECT user_id FROM accounts"):
            self._r = (UID,)
        elif k.startswith("SELECT MAX(created_at) FROM notification_sends"):
            self._r = (self.db["gift_sent_at"],)
        elif k.startswith("SELECT 1 FROM question_wheel_spins"):
            self._r = (1,) if self.db["spins"] else None
        elif k.startswith("INSERT INTO question_wheel_spins"):
            self.db["spins"].append(params)
        elif k.startswith("INSERT INTO rewarded_entitlements"):
            pass
        elif k.startswith("UPDATE rewarded_entitlements"):
            self.db["questions"] += 1
            self._r = (self.db["questions"],)
        else:
            raise AssertionError("SQL non géré : " + k)


class _Conn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return _Cur(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def env(monkeypatch):
    db = {"gift_sent_at": A._utcnow() - timedelta(hours=2), "spins": [],
          "questions": 0, "premium": False, "distress": False}
    monkeypatch.setattr(A, "get_conn", lambda: _Conn(db))
    monkeypatch.setattr(A, "resolve_app_session",
                        lambda tok: {"session_id": "s", "user_id": UID, "email": "x@x.co"} if tok else None)
    monkeypatch.setattr(A, "_explorer_premium_tx", lambda c, u, n: db["premium"])
    monkeypatch.setattr(A, "_gift_wheel_distress_blocked", lambda u, n: db["distress"])
    A.app.config["TESTING"] = True
    A.limiter.enabled = False
    return db, A.app.test_client()


def _post(client, body=None):
    r = client.post("/api/app/rewards/question-wheel", json=body or {},
                    headers={"Authorization": "Bearer x"})
    return r.get_json()


def test_check_then_spin_gives_exactly_one_question(env):
    db, client = env
    assert _post(client, {"check": True})["eligible"] is True
    assert db["questions"] == 0
    j = _post(client)
    assert j["credited"] is True and j["questions_available"] == 1
    j = _post(client)
    assert j["credited"] is False and j["reason"] == "already_used"
    assert db["questions"] == 1


@pytest.mark.parametrize("field,value,reason", [
    ("gift_sent_at", None, "no_gift"),
    ("premium", True, "premium"),
    ("distress", True, "not_eligible"),
])
def test_refusals(env, field, value, reason):
    db, client = env
    db[field] = value
    j = _post(client)
    assert j["eligible"] is False and j["reason"] == reason
    assert db["questions"] == 0


def test_migration_is_wired():
    import inspect
    assert "055_question_wheel.sql" in inspect.getsource(A.init_db)


def test_offers_switch_route_and_migration():
    import inspect
    src = inspect.getsource(A.api_push_offers_pref)
    assert "push_offers_enabled" in src and "isinstance(body.get(\"enabled\"), bool)" in src
    assert "056_push_offers_pref.sql" in inspect.getsource(A.init_db)
