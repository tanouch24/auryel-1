import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import auryel_bot as A

RID = "3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90"
UID = "11111111-2222-3333-4444-555555555555"


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, query, args):
        self.calls.append((query, args))

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, row):
        self.cur = _Cursor(row)

    def cursor(self):
        return self.cur

    def close(self):
        pass


def test_tag_is_stable_and_names_the_reading():
    assert A._ai_report_explorer_tag("palm", RID) == f"[explorer:palm reading={RID}]"


def test_owned_checks_structured_table_with_type(monkeypatch):
    conn = _Conn((1,))
    monkeypatch.setattr(A, "get_conn", lambda: conn)
    assert A._ai_report_explorer_owned(UID, "coffee", RID) is True
    query, args = conn.cur.calls[0]
    assert "FROM explorer_structured_readings" in query
    assert "user_id=%s" in query and "experience_type=%s" in query
    assert args == (RID, UID, "coffee")


def test_owned_uses_dedicated_tables_for_crystal_and_tarot(monkeypatch):
    for exp, table in (("crystal_ball", "crystal_ball_readings"), ("tarot", "tirages")):
        conn = _Conn((1,))
        monkeypatch.setattr(A, "get_conn", lambda conn=conn: conn)
        assert A._ai_report_explorer_owned(UID, exp, RID) is True
        query, args = conn.cur.calls[0]
        assert f"FROM {table}" in query
        assert args == (RID, UID)


def test_owned_rejects_other_account_unknown_type_and_bad_id(monkeypatch):
    monkeypatch.setattr(A, "get_conn", lambda: _Conn(None))
    assert A._ai_report_explorer_owned(UID, "palm", RID) is False
    assert A._ai_report_explorer_owned(UID, "soulmate", RID) is False
    assert A._ai_report_explorer_owned(UID, "palm", "not-a-uuid") is False
