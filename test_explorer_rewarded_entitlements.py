import os
from pathlib import Path

os.environ.setdefault("TAROT_MEDIA_UPLOAD_DISABLED", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import auryel_bot as A


def test_all_explorer_experience_types_are_whitelisted_independently():
    expected = {"tarot", "crystal_ball", "dreams", "compatibility",
                "palm", "coffee", "soulmate"}
    assert expected == set(A._EXPLORER_EXPERIENCE_TYPES)
    for item in expected:
        assert A._validate_explorer_experience_type(item) == item


def test_invalid_explorer_purpose_is_rejected():
    assert A._validate_explorer_experience_type("consultation") is None
    assert A._validate_explorer_experience_type("explorer_tarot") is None
    assert A._validate_explorer_experience_type("") is None


def test_explorer_migration_is_additive_and_account_owned():
    sql = Path("migrations/049_explorer_rewarded_entitlements.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS purpose" in sql
    assert "CREATE TABLE IF NOT EXISTS explorer_generation_usage" in sql
    assert "CREATE TABLE IF NOT EXISTS explorer_reward_entitlements" in sql
    assert "REFERENCES accounts(user_id)" in sql
    assert "DROP TABLE" not in sql.upper()


def test_explorer_migration_has_one_entitlement_per_reward_session():
    sql = Path("migrations/049_explorer_rewarded_entitlements.sql").read_text()
    assert "UNIQUE (reward_session_id)" in sql
    assert "status IN ('available', 'consumed')" in sql


def test_explorer_reward_does_not_use_consultation_credit_function():
    source = Path("auryel_bot.py").read_text()
    explorer_branch = source[source.index("def api_admob_reward_ssv"):source.index("@app.route(\"/api/app/rewards/wallet\"")]
    assert "_credit_explorer_entitlement_tx" in explorer_branch
    assert "_credit_rewarded_entitlement_tx(c, uid, sid" not in explorer_branch


def test_explorer_ssv_purpose_is_resolved_from_session_not_callback():
    source = Path("auryel_bot.py").read_text()
    assert "session_row[3] == \"consultation_question\"" in source
    assert "session_row[3].startswith(_EXPLORER_REWARD_PREFIX)" in source
    assert "experience_type" in source[source.index("def api_admob_reward_session"):source.index("@app.route(\"/api/app/rewards/admob/session/<session_id>\"")]


def test_explorer_entitlement_credit_is_idempotent_at_schema_level():
    class Cursor:
        rowcount = 1

        def __init__(self):
            self.created = True
            self.statements = []

        def execute(self, sql, args):
            self.statements.append((sql, args))

        def fetchone(self):
            if self.created:
                self.created = False
                return ("entitlement-1",)
            return None

    cursor = Cursor()
    first = A._credit_explorer_entitlement_tx(
        cursor, "user-1", "session-1", "tarot", A._utcnow())
    second = A._credit_explorer_entitlement_tx(
        cursor, "user-1", "session-1", "tarot", A._utcnow())
    assert first["explorer_entitlement_created"] is True
    assert second["explorer_entitlement_created"] is False


def test_explorer_entitlement_consumption_is_single_use():
    class Cursor:
        rowcount = 1
        def execute(self, sql, args):
            self.sql = sql
            self.args = args

    reservation = {"allowed": True, "mode": "entitlement",
                   "entitlement_id": "entitlement-1"}
    cursor = Cursor()
    A._finalize_explorer_generation_tx(
        cursor, "user-1", "tarot", reservation, A._utcnow())
    assert "status='consumed'" in cursor.sql
    assert cursor.args[1] == "entitlement-1"


class _AccessCursor:
    rowcount = 1

    def __init__(self, fetches):
        self.fetches = list(fetches)
        self.statements = []

    def execute(self, sql, args):
        self.statements.append((sql, args))

    def fetchone(self):
        return self.fetches.pop(0)


def test_first_free_is_per_experience_and_reserved_before_generation():
    cursor = _AccessCursor([None, (0,)])
    result = A._explorer_generation_access_tx(
        cursor, "user-1", "tarot", A._utcnow())
    assert result == {"allowed": True, "mode": "first_free",
                      "entitlement_id": None}


def test_used_experience_without_entitlement_requires_reward():
    cursor = _AccessCursor([None, (1,), None])
    result = A._explorer_generation_access_tx(
        cursor, "user-1", "dreams", A._utcnow())
    assert result["allowed"] is False
    assert result["mode"] == "reward_required"


def test_available_explorer_entitlement_is_reserved_for_its_experience():
    cursor = _AccessCursor([None, (1,), ("entitlement-1",)])
    result = A._explorer_generation_access_tx(
        cursor, "user-1", "crystal_ball", A._utcnow())
    assert result == {"allowed": True, "mode": "entitlement",
                      "entitlement_id": "entitlement-1"}


def test_consultation_reward_state_transition_remains_unchanged():
    state = A._apply_rewarded_entitlement_credit(0, 9, 9, 0)
    assert state["questions_available"] == 1
    assert state["progress"] == 0
    assert state["minutes_awarded"] == 5


def test_reopen_and_talk_to_guide_are_not_generation_consumers():
    source = Path("auryel_bot.py").read_text()
    assert "explorer_generation_attempts" in source
    assert "explorer_context_attached" not in source
