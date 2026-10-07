import json
import os
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import auryel_bot as A


def test_structured_dream_generation_is_one_call_and_bounded(monkeypatch):
    calls = []

    def fake_call(messages, temperature, max_tokens):
        calls.append((messages, temperature, max_tokens))
        return json.dumps({
            "title": "Une rive intérieure",
            "symbols": "L'eau et le passage",
            "atmosphere": "Une énergie calme",
            "interpretation": "Une invitation à observer",
            "reflection": "Qu'est-ce qui demande de l'espace ?",
        })

    monkeypatch.setattr(A, "_call_llm_once", fake_call)
    result = A._structured_explorer_generate_once("dreams", {"dream": "une mer"})
    assert len(calls) == 1
    assert set(result) == {"title", "symbols", "atmosphere", "interpretation", "reflection"}


def test_structured_compatibility_generation_rejects_non_json(monkeypatch):
    monkeypatch.setattr(A, "_call_llm_once", lambda *args, **kwargs: "nope")
    try:
        A._structured_explorer_generate_once(
            "compatibility", {"other_name": "A", "relation": "ami"}
        )
    except RuntimeError as exc:
        assert str(exc) == "explorer_invalid_generation"
    else:
        raise AssertionError("invalid structured output must fail")


def test_structured_migration_is_additive_and_account_scoped():
    sql = Path("migrations/051_explorer_dreams_compatibility.sql").read_text()
    assert "explorer_structured_readings" in sql
    assert "REFERENCES accounts(user_id) ON DELETE CASCADE" in sql
    assert "UNIQUE (user_id, experience_type, idempotency_key)" in sql
    assert "DROP TABLE" not in sql.upper()


def test_daily_home_feature_is_server_day_deterministic():
    source = Path("auryel_bot.py").read_text()
    block = source[source.index("_EXPLORER_DAILY_FEATURES"):source.index("def _crystal_theme")]
    assert "_wellbeing_day(_utcnow())" in block
    assert "random" not in block.lower()
    for experience in (
        "tarot", "crystal_ball", "dreams", "compatibility", "palm", "coffee"
    ):
        assert f'"{experience}"' in block


def test_photo_reading_migration_only_expands_structured_types():
    sql = Path("migrations/052_explorer_photo_readings.sql").read_text()
    assert "DROP CONSTRAINT IF EXISTS explorer_structured_type_valid" in sql
    assert "'palm'" in sql and "'coffee'" in sql
    assert "DROP TABLE" not in sql.upper()


def test_structured_context_is_server_scoped_and_attached_on_real_message():
    source = Path("auryel_bot.py").read_text()
    assert "WHERE id=%s AND user_id=%s" in source
    assert "render_structured_explorer_context" in source
    assert "explorer_context_id" in source
