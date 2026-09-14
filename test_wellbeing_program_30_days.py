"""Contrat du programme Bien-être 30 jours.

Ces contrôles restent indépendants de PostgreSQL : ils verrouillent la
migration et les routes avant l’intégration à l’environnement Railway.
"""

from pathlib import Path
import re


ROOT = Path(__file__).parent
MIGRATION = (ROOT / "migrations/027_wellbeing_program_30_days.sql").read_text()
SOURCE = (ROOT / "auryel_bot.py").read_text()


def test_migration_is_additive_and_seeds_150_advice_rows():
    assert "CREATE TABLE IF NOT EXISTS wellbeing_programs" in MIGRATION
    assert "CREATE TABLE IF NOT EXISTS wellbeing_program_days" in MIGRATION
    assert "CREATE TABLE IF NOT EXISTS wellbeing_program_actions" in MIGRATION
    assert "CREATE TABLE IF NOT EXISTS wellbeing_program_advice" in MIGRATION
    assert "CREATE TABLE IF NOT EXISTS wellbeing_program_ebook_config" in MIGRATION
    assert "ON CONFLICT (id) DO NOTHING" in MIGRATION
    advice_section = MIGRATION.split("INSERT INTO wellbeing_program_advice", 1)[1]
    advice_section = advice_section.split("ON CONFLICT", 1)[0]
    assert len(re.findall(r"\(\d+, '", advice_section)) == 150
    assert "wellbeing_program_advice" in MIGRATION


def test_program_contract_has_exactly_five_actions_and_no_reward_tables():
    assert "_WELLBEING_PROGRAM_ACTIONS_PER_DAY = 5" in SOURCE
    assert "generate_series(1, 30)" in SOURCE
    assert "generate_series(1, 5)" in SOURCE
    block = SOURCE[SOURCE.index("def _wellbeing_program_payload"):SOURCE.index("# PARCOURS BIEN-ÊTRE")]
    assert "stars" not in block.lower()
    assert "time_wallet" not in block.lower()
    assert "consultation" not in block.lower()


def test_program_routes_and_idempotent_completion_are_registered():
    for route, method in (
        ("/api/app/wellbeing-program", "GET"),
        ("/api/app/wellbeing-program/start", "POST"),
        ("/api/app/wellbeing-program/day/<int:day_number>/action/<int:action_slot>", "POST"),
        ("/api/app/wellbeing-program/reminder", "POST"),
    ):
        assert f'@app.route("{route}", methods=["{method}"])' in SOURCE
    assert "COALESCE(completed_at, %s)" in SOURCE
    assert "wellbeing_daily" in SOURCE


def test_account_deletion_includes_program_data():
    for table in ("wellbeing_program_actions", "wellbeing_program_days", "wellbeing_programs"):
        assert table in SOURCE
