"""Fail-closed contract tests for admin MFA bootstrap sessions."""

from pathlib import Path


SOURCE = Path(__file__).with_name("admin_control.py").read_text(encoding="utf-8")
MIGRATION = Path(__file__).with_name("migrations").joinpath("046_admin_mfa_bootstrap.sql").read_text(encoding="utf-8")


def test_bootstrap_session_has_a_short_lived_restricted_scope():
    assert 'session_scope = "full" if row[5] else "mfa_bootstrap"' in SOURCE
    assert "MFA_BOOTSTRAP_TTL = timedelta(minutes=10)" in SOURCE
    assert 'auth["scope"] != "full"' in SOURCE


def test_only_identity_and_mfa_routes_accept_bootstrap_scope():
    assert "@admin_bootstrap_or_full_required\n    def admin_logout" in SOURCE
    assert "@admin_bootstrap_or_full_required\n    def admin_me" in SOURCE
    assert "@admin_bootstrap_or_full_required\n    def admin_mfa_enroll" in SOURCE
    assert "@admin_bootstrap_or_full_required\n    def admin_mfa_confirm" in SOURCE
    assert "@admin_required\n    @_with_audit\n    def admin_overview" in SOURCE
    assert 'auth["scope"] not in ("mfa_bootstrap", "full")' in SOURCE


def test_confirmation_promotes_only_the_current_bootstrap_session():
    assert "UPDATE admin_sessions SET scope='full'" in SOURCE
    assert "WHERE id=%s AND revoked_at IS NULL" in SOURCE
    assert '"mfa_verified": g.admin_auth["scope"] == "full"' in SOURCE


def test_migration_is_additive_and_existing_sessions_default_full():
    assert "ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'full'" in MIGRATION
    assert "a.mfa_enabled = FALSE" in MIGRATION
    assert "scope = 'mfa_bootstrap'" in MIGRATION
    assert "CREATE INDEX IF NOT EXISTS" in MIGRATION
    assert "DROP TABLE" not in MIGRATION.upper()


def test_bootstrap_never_grants_business_access_by_contract():
    business_routes = (
        "admin_overview", "admin_users", "admin_user_detail", "admin_billing",
        "admin_consultations", "admin_advisors", "admin_stars", "admin_content",
        "admin_notifications", "admin_technical", "admin_ai",
        "admin_analytics_activity", "admin_analytics_funnel",
        "admin_analytics_retention", "admin_analytics_content",
        "admin_analytics_wake", "admin_analytics_acquisition",
    )
    for route in business_routes:
        start = SOURCE.index(f"def {route}")
        preceding = SOURCE[max(0, start - 140):start]
        assert "@admin_required" in preceding, route
