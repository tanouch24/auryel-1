"""Local security contract tests for the Auryel Control boundary."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()
for key, value in {
    "SECRET_KEY": "test-admin-secret",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "ADMIN_PASSWORD": "legacy-only-test-value",
}.items():
    os.environ.setdefault(key, value)

import auryel_bot as app_module


def test_admin_routes_are_private_and_complete():
    routes = {(rule.rule, method) for rule in app_module.app.url_map.iter_rules() for method in rule.methods}
    assert ("/api/admin/auth/login", "POST") in routes
    assert ("/api/admin/auth/logout", "POST") in routes
    assert ("/api/admin/auth/me", "GET") in routes
    assert ("/api/admin/auth/mfa/enroll", "POST") in routes
    assert ("/api/admin/auth/mfa/confirm", "POST") in routes
    assert ("/api/admin/auth/mfa/recovery-codes", "POST") in routes
    assert ("/api/admin/auth/mfa/disable", "POST") in routes
    assert ("/api/admin/overview", "GET") in routes
    assert ("/api/admin/users", "GET") in routes
    assert ("/api/admin/billing/overview", "GET") in routes
    client = app_module.app.test_client()
    response = client.get("/api/admin/overview")
    assert response.status_code == 401


def test_admin_sql_contract_excludes_private_message_content():
    source = Path(__file__).with_name("admin_control.py").read_text(encoding="utf-8")
    assert "messages.content" not in source
    assert '"password_hash"' not in source
    assert "session_token" in source
    assert "estimated_gross_revenue" in source
    assert "financial_accuracy" in source


def test_legacy_mobile_bearer_is_not_admin_authentication():
    client = app_module.app.test_client()
    response = client.get("/api/admin/auth/me", headers={"Authorization": "Bearer mobile-token"})
    assert response.status_code == 401


def test_admin_cors_is_allowlisted_and_preflight_does_not_use_wildcard():
    client = app_module.app.test_client()
    response = client.options(
        "/api/admin/overview",
        headers={
            "Origin": "https://dashboard.auryelvoyance.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert response.status_code in (200, 204)
    assert response.headers.get("Access-Control-Allow-Origin") == "https://dashboard.auryelvoyance.com"
    assert response.headers.get("Access-Control-Allow-Origin") != "*"
    refused = client.options(
        "/api/admin/overview",
        headers={
            "Origin": "https://not-auryel.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert refused.headers.get("Access-Control-Allow-Origin") is None


def test_analytics_admin_routes_remain_private():
    client = app_module.app.test_client()
    for path in (
        "/api/admin/analytics/activity",
        "/api/admin/analytics/funnel",
        "/api/admin/analytics/retention",
        "/api/admin/analytics/content",
        "/api/admin/analytics/wake",
        "/api/admin/analytics/acquisition",
    ):
        assert client.get(path).status_code == 401
