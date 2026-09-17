"""Contrat du catalogue d'ebooks Bien-être, sans accès à une base réelle."""

from pathlib import Path
import ast


ROOT = Path(__file__).parent
MIGRATION = (ROOT / "migrations/028_wellbeing_ebook_library.sql").read_text()
SOURCE = (ROOT / "auryel_bot.py").read_text()


def _ebook_dict_for_test():
    tree = ast.parse(SOURCE)
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_wellbeing_ebook_dict"
    )
    namespace = {"_R2_PUBLIC_BASE_URL": "https://cdn.example/base",
                 "_url_quote": lambda value, safe="": value.replace("/", "%2F")}
    exec(compile(ast.Module(body=[function], type_ignores=[]),
                 str(ROOT / "auryel_bot.py"), "exec"), namespace)
    return namespace["_wellbeing_ebook_dict"]


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
    assert "publication_date <= CURRENT_DATE" in block
    assert "sort_order ASC" in block
    assert "@require_app_auth" in SOURCE[SOURCE.index('@app.route("/api/app/wellbeing-ebooks"'):]


def test_catalogue_has_free_and_premium_neutral_access_and_no_rewards_logic():
    route = SOURCE[SOURCE.index('@app.route("/api/app/wellbeing-ebooks"'):
                   SOURCE.index('@app.route("/api/app/wellbeing-program/start"')]
    assert "_auth_json" in route
    assert "stars" not in route.lower()
    assert "premium" not in route.lower()
    assert "wellbeing_ebooks" in route


def test_ebook_dict_prefers_explicit_cover_and_preserves_pdf_url():
    build = _ebook_dict_for_test()
    row = (3, "quand-la-tete-refuse-de-dormir", "Quand la tête refuse de dormir",
           "", "", "https://explicit.example/cover.webp",
           "https://cdn.example/ebooks/002.pdf", None, None, "1", True)
    result = build(row)
    assert result["cover_url"] == row[5]
    assert result["pdf_url"] == row[6]
    assert result["active"] is True


def test_ebook_dict_builds_safe_r2_cover_fallback():
    build = _ebook_dict_for_test()
    row = (3, "quand/la-tete", "Titre", "", "", None,
           "https://cdn.example/ebooks/002.pdf", None, None, "1", True)
    result = build(row)
    assert result["cover_url"] == (
        "https://cdn.example/base/auryel-ebook-covers/quand%2Fla-tete.webp"
    )
    assert result["pdf_url"] == row[6]
    assert result["active"] is True


def test_catalogue_uses_the_same_cover_fallback_contract():
    block = SOURCE[SOURCE.index("def _wellbeing_ebook_catalog"):
                   SOURCE.index('@app.route("/api/app/wellbeing-ebooks"')]
    assert 'ebook = _wellbeing_ebook_dict(row)' in block
    assert '"cover_url": _wellbeing_ebook_media_url(ebook["cover_url"], row[13])' in block
