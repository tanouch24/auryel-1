"""Réponse en suspens (09/10/2026) — POST /api/consultation/teaser.

Temps épuisé : seule la 1re phrase de la réponse du guide est transmise,
une fois par jour, jamais en détresse ; après Premium, la réponse complète
reprend exactement cette phrase (teaser_id sur /api/consultation/message).
"""
import os
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("psycopg2", MagicMock())
for _k, _v in {"SECRET_KEY": "t", "ADMIN_PASSWORD": "t",
               "DATABASE_URL": "postgresql://t:t@127.0.0.1:1/t"}.items():
    os.environ.setdefault(_k, _v)

import pytest  # noqa: E402

import auryel_bot as A  # noqa: E402

UID = "11111111-1111-4111-8111-111111111111"
REPLY = ("Je ne le vois pas revenir pour l'instant. Ce message montre qu'il "
         "pense à toi, mais pas assez pour faire le pas. Attends ses actes.")


class _Cur:
    def __init__(self, db):
        self.db = db
        self._r = None

    def fetchone(self):
        return self._r

    def execute(self, sql, params=()):
        k = " ".join(sql.split())
        if k.startswith("SELECT COUNT(*) FROM consultation_teasers"):
            self._r = (self.db["today"],)
        elif k.startswith("INSERT INTO consultation_teasers"):
            self.db["inserted"].append(params)
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
    db = {"today": 0, "inserted": [], "remaining": 0, "llm": REPLY,
          "outcome": "success", "safety": False, "llm_calls": 0}

    def llm(messages, temperature=0.85, max_tokens=320, mode="normal"):
        db["llm_calls"] += 1
        return db["llm"]

    monkeypatch.setattr(A, "get_conn", lambda: _Conn(db))
    monkeypatch.setattr(A, "resolve_app_session",
                        lambda tok: {"session_id": "s", "user_id": UID, "email": "x@x.co"} if tok else None)
    monkeypatch.setattr(A, "_adult_gate_check", lambda uid, today=None: None)
    monkeypatch.setattr(A, "get_or_create_app_profile", lambda uid: {"guide": "selena", "prenom": "Camille"})
    monkeypatch.setattr(A, "_teaser_blocked_for_safety", lambda p, m, n: db["safety"])
    monkeypatch.setattr(A, "_get_time_snapshot_tx",
                        lambda c, u, n: {"total_remaining_seconds": db["remaining"]})
    monkeypatch.setattr(A, "get_history_for_user_id", lambda uid, limit=20, consultation_id=None: [])
    monkeypatch.setattr(A, "get_system_prompt", lambda *a, **k: "SYSTEM")
    monkeypatch.setattr(A, "call_llm", llm)
    monkeypatch.setattr(A, "llm_last_outcome", lambda: db["outcome"])
    A.app.config["TESTING"] = True
    A.limiter.enabled = False
    return db, A.app.test_client()


def _post(client, msg="Il va revenir ?"):
    r = client.post("/api/consultation/teaser", json={"message": msg},
                    headers={"Authorization": "Bearer x"})
    return r.status_code, r.get_json()


def test_only_the_first_sentence_leaves_the_server(env):
    db, client = env
    code, j = _post(client)
    assert code == 200 and j["eligible"] is True
    assert j["first_sentence"] == "Je ne le vois pas revenir pour l'instant."
    assert "Attends ses actes" not in str(j)
    assert len(db["inserted"]) == 1 and db["inserted"][0][3] == j["first_sentence"]


@pytest.mark.parametrize("field,value,reason", [
    ("safety", True, "not_eligible"),
    ("remaining", 120, "time_left"),
    ("today", 1, "daily_limit"),
])
def test_refusals_never_call_the_llm(env, field, value, reason):
    db, client = env
    db[field] = value
    code, j = _post(client)
    assert j == {"eligible": False, "reason": reason}
    assert db["llm_calls"] == 0 and db["inserted"] == []


def test_llm_failure_or_one_sentence_reply_is_not_teased(env):
    db, client = env
    db["outcome"] = "fallback_failure"
    assert _post(client)[1]["reason"] == "unavailable"
    db["outcome"], db["llm"] = "success", "Oui."
    assert _post(client)[1]["reason"] == "too_short"
    assert db["inserted"] == []


def test_first_sentence_helper():
    assert A._teaser_first_sentence("Un. Deux.") == "Un."
    assert A._teaser_first_sentence("Une seule phrase") is None
    long = "mot " * 100 + ". Fin."
    assert len(A._teaser_first_sentence(long)) <= A._TEASER_MAX_CHARS + 1


def test_safety_check_blocks_acute_distress_message():
    profile = {"guide": "selena"}
    assert A._teaser_blocked_for_safety(profile, "j'ai envie d'en finir", A._utcnow()) is True


def test_message_route_resumes_the_exact_sentence():
    import inspect
    src = inspect.getsource(A.api_consultation_message)
    assert "RÉPONSE DÉJÀ COMMENCÉE" in src and "_consume_teaser" in src
    sql = open(os.path.join(os.path.dirname(__file__), "migrations",
                            "054_consultation_teasers.sql")).read()
    assert "CREATE TABLE IF NOT EXISTS consultation_teasers" in sql
    assert "054_consultation_teasers.sql" in inspect.getsource(A.init_db)
