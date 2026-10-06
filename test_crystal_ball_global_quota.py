import os
import subprocess
from pathlib import Path

os.environ.setdefault("TAROT_MEDIA_UPLOAD_DISABLED", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import auryel_bot as A


class AccessCursor:
    rowcount = 1

    def __init__(self, fetches):
        self.fetches = list(fetches)
        self.statements = []

    def execute(self, sql, args):
        self.statements.append((sql, args))

    def fetchone(self):
        return self.fetches.pop(0)


def test_server_day_is_paris_and_global_access_is_shared():
    before_midnight = A.datetime(2026, 10, 6, 21, 59, tzinfo=A.timezone.utc)
    after_midnight = A.datetime(2026, 10, 6, 22, 1, tzinfo=A.timezone.utc)
    assert A._wellbeing_day(before_midnight).isoformat() == "2026-10-06"
    assert A._wellbeing_day(after_midnight).isoformat() == "2026-10-07"
    first = A._explorer_generation_access_tx(
        AccessCursor([None, (0,)]), "u", "tarot", before_midnight)
    blocked = A._explorer_generation_access_tx(
        AccessCursor([None, (1,)]), "u", "crystal_ball", before_midnight)
    assert first["mode"] == "daily_free"
    assert blocked["mode"] == "daily_quota_exhausted"


def test_premium_bypasses_global_quota():
    reservation = A._explorer_generation_access_tx(
        AccessCursor([(1,)]), "premium", "crystal_ball", A._utcnow())
    assert reservation == {"allowed": True, "mode": "premium", "entitlement_id": None}


def test_failed_generation_can_leave_quota_unfinalized():
    cursor = AccessCursor([])
    reservation = {"allowed": True, "mode": "daily_free", "usage_day": A._wellbeing_day()}
    A._finalize_explorer_generation_tx(cursor, "u", "tarot", reservation, A._utcnow())
    assert any("explorer_daily_generation_usage" in sql for sql, _ in cursor.statements)


def test_crystal_structured_generation_uses_one_model_call(monkeypatch):
    calls = []

    def fake_call(messages, temperature, max_tokens):
        calls.append((messages, temperature, max_tokens))
        return '{"vision_title":"Ouverture","vision":"Une image.","interpretation":"Un angle.","guidance":"Un pas."}'

    monkeypatch.setattr(A, "_call_llm_once", fake_call)
    result = A._crystal_generate_once("Quelle direction ?", "direction")
    assert len(calls) == 1
    assert set(result) == {"vision_title", "vision", "interpretation", "guidance"}


def test_crystal_generation_invalid_model_output_fails_without_fallback(monkeypatch):
    monkeypatch.setattr(A, "_call_llm_once", lambda *args, **kwargs: "not-json")
    try:
        A._crystal_generate_once("Question", "clarté")
    except RuntimeError as exc:
        assert str(exc) == "crystal_invalid_generation"
    else:
        raise AssertionError("invalid model output must fail the transaction")


def test_crystal_routes_are_authenticated_and_account_scoped():
    source = Path("auryel_bot.py").read_text()
    create = source[source.index("def api_crystal_ball_create"):source.index("def api_crystal_ball_get")]
    fetch = source[source.index("def api_crystal_ball_get"):source.index("@app.route(\"/api/tirages\", methods=[\"GET\"])")]
    assert "@require_app_auth" in create
    assert "user_id=%s AND idempotency_key=%s" in create
    assert "WHERE id=%s AND user_id=%s" in fetch
    assert "add_message_for_user_id" not in create
    assert "render_crystal_context" in source
    assert "explorer_context_id" in source


def test_crystal_migration_is_additive_and_cascades_by_account():
    sql = subprocess.check_output([
        "git", "show", "HEAD:migrations/050_explorer_global_daily_crystal.sql"
    ], text=True)
    assert "explorer_daily_generation_usage" in sql
    assert "crystal_ball_readings" in sql
    assert "REFERENCES accounts(user_id) ON DELETE CASCADE" in sql
    assert "UNIQUE (user_id, idempotency_key)" in sql
    assert "DROP TABLE" not in sql.upper()


def test_rewarded_path_remains_in_source_for_later_reactivation():
    source = Path("auryel_bot.py").read_text()
    assert "_credit_explorer_entitlement_tx" in source
    assert "explorer_reward_required" in source
    assert "daily_quota_exhausted" in source
