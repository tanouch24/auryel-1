"""Contrats locaux de la source de vérité unread.

Lecture statique uniquement : aucun accès DB ou réseau réel.
"""
from pathlib import Path


ROOT = Path(__file__).parent
SQL = (ROOT / "migrations/040_unread_events.sql").read_text()
BOT = (ROOT / "auryel_bot.py").read_text()
SCHEDULER = (ROOT / "push_scheduler.py").read_text()


def test_unread_schema_is_idempotent_and_account_scoped():
    assert "CREATE TABLE IF NOT EXISTS unread_events" in SQL
    assert "user_id       UUID NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE" in SQL
    assert "UNIQUE (user_id, dedupe_key)" in SQL
    assert "read_at       TIMESTAMPTZ NULL" in SQL
    assert "WHERE read_at IS NULL" in SQL


def test_authenticated_count_and_read_routes_exist():
    assert '@app.route("/api/app/unread", methods=["GET"])' in BOT
    assert '@app.route("/api/app/unread/read", methods=["POST"])' in BOT
    block = BOT[BOT.index("def api_app_unread"):BOT.index("_APP_MESSAGE_MAX_LEN")]
    assert "@require_app_auth" in BOT[BOT.index('@app.route("/api/app/unread"'):]
    assert "GROUP BY category" in block
    assert "read_at=COALESCE(read_at, NOW())" in block


def test_push_events_create_openable_unread_items_idempotently():
    assert "ON CONFLICT (user_id, dedupe_key) DO NOTHING" in BOT
    assert '"personal_guidance", "wellbeing_session", "ebook_monthly"' in SCHEDULER
    assert '"consultation" if category == "personal_guidance"' in SCHEDULER
    assert '"wellbeing"' in SCHEDULER


def test_account_deletion_includes_unread_events():
    start = BOT.index("_ACCOUNT_DELETE_CHILD_TABLES = (")
    block = BOT[start:start + 1800]
    assert '"unread_events"' in block
