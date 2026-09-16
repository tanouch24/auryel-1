"""Contract for the retired Stars-based Consultation Express route."""

import os
import sys
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()
for _key, _value in {
    "SECRET_KEY": "test-secret-key",
    "ADMIN_PASSWORD": "test",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
}.items():
    os.environ.setdefault(_key, _value)

import auryel_bot as A

A.app.config["TESTING"] = True
try:
    A.limiter.enabled = False
except Exception:
    pass


def test_stars_consultation_express_is_retired():
    A.resolve_app_session = lambda token: (
        {"session_id": "s", "user_id": "11111111-1111-4111-8111-111111111111"}
        if token else None
    )
    client = A.app.test_client()
    response = client.post(
        "/api/app/rewards/express-consultation",
        headers={"Authorization": "Bearer test"},
        json={"product_key": "express_consultation_10min", "idempotency_key": "k"},
    )
    assert response.status_code == 410
    assert response.get_json() == {"error": "stars_economy_retired"}


def test_retired_route_does_not_expose_an_active_purchase_contract():
    source = open("auryel_bot.py", encoding="utf-8").read()
    assert '"stars_economy_retired"' in source
    assert 'return _auth_json({"error": "stars_economy_retired"}, 410)' in source
