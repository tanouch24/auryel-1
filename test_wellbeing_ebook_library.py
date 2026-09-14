"""Contrat du catalogue d'ebooks Bien-être, sans accès à une base réelle."""

from pathlib import Path


ROOT = Path(__file__).parent
MIGRATION = (ROOT / "migrations/028_wellbeing_ebook_library.sql").read_text()
SOURCE = (ROOT / "auryel_bot.py").read_text()


def test_catalogue_is_additive_and_idempotent():
    assert "CREATE TABLE IF NOT EXISTS wellbeing_ebooks" in MIGRATION
    assert "ALTER TABLE wellbeing_program_ebook_config" in MIGRATION
    assert "ADD COLUMN IF NOT EXISTS ebook_id" in MIGRATION
    assert "ON CONFLICT (id) DO NOTHING" in MIGRATION
    assert "notification_sent_at" in MIGRATION
    assert "push_type TEXT NOT NULL DEFAULT 'ebook_monthly'" in MIGRATION
    assert "DROP" not in MIGRATION.upper()
    assert "TRUNCATE" not in MIGRATION.upper()


def test_first_ebook_r2_urls_are_reproducible_and_slug_scoped():
    migration = (ROOT / "migrations/029_wellbeing_first_ebook_r2.sql").read_text()
    assert "WHERE slug = '30-jours-pour-prendre-soin-de-soi'" in migration
    assert "pub-19c78d4dc57a41849a27c0e73ed231ce.r2.dev/ebooks/" in migration
    assert migration.count("pdf_url = 'https://") == 1
    assert migration.count("cover_url = 'https://") == 1
    assert "DROP" not in migration.upper()


def test_first_ebook_is_shared_with_the_program():
    assert "30-jours-pour-prendre-soin-de-soi" in MIGRATION
    assert "UPDATE wellbeing_program_ebook_config" in MIGRATION
    assert "SET ebook_id = 1" in MIGRATION
    assert "LEFT JOIN wellbeing_ebooks e ON e.id=c.ebook_id" in SOURCE


def test_catalogue_route_filters_active_published_entries_and_sorts_newest_first():
    assert '@app.route("/api/app/wellbeing-ebooks", methods=["GET"])' in SOURCE
    block = SOURCE[SOURCE.index("def _wellbeing_ebook_catalog"):SOURCE.index("@app.route(\"/api/app/wellbeing-ebooks\"")]
    assert "active=TRUE" in block
    assert "publication_date <= %s" in block
    assert "publication_date DESC" in block
    assert "@require_app_auth" in SOURCE[SOURCE.index('@app.route("/api/app/wellbeing-ebooks"'):]


def test_catalogue_has_free_and_premium_neutral_access_and_no_rewards_logic():
    route = SOURCE[SOURCE.index('@app.route("/api/app/wellbeing-ebooks"'):
                   SOURCE.index('@app.route("/api/app/wellbeing-program/start"')]
    assert "_auth_json" in route
    assert "stars" not in route.lower()
    assert "premium" not in route.lower()
    assert "wellbeing_ebooks" in route
