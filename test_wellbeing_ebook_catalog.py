"""Tests locaux du catalogue dynamique d'ebooks Bien-être."""

import json
import os
import sys
from datetime import date
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()
for _key, _value in {
    "SECRET_KEY": "test-secret-key",
    "ADMIN_PASSWORD": "test",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
}.items():
    os.environ.setdefault(_key, _value)

import auryel_bot as A


ROOT = os.path.dirname(os.path.abspath(A.__file__))


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.result = []

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        assert "FROM wellbeing_ebooks" in normalized
        assert "active=TRUE" in normalized
        assert "publication_date <= CURRENT_DATE" in normalized
        assert "ORDER BY sort_order ASC" in normalized
        self.result = self.rows

    def fetchall(self):
        return self.result


class Connection:
    def __init__(self, rows):
        self.cursor_instance = Cursor(rows)

    def cursor(self):
        return self.cursor_instance

    def close(self):
        pass


def _row(ebook_id, slug, title, *, cover_key=None, object_key=None):
    return (
        ebook_id, slug, title, "Sous-titre", "Description", None,
        None, date(2026, 9, 16), "Septembre", "1", True, False,
        object_key, cover_key, "Sommeil", "Auryel", 1,
    )


def main():
    migration = open(
        os.path.join(ROOT, "migrations/037_wellbeing_ebook_catalog.sql"),
        encoding="utf-8",
    ).read()
    assert "CREATE TABLE IF NOT EXISTS wellbeing_ebooks" in migration
    assert "ADD COLUMN IF NOT EXISTS object_key" in migration
    assert "ADD COLUMN IF NOT EXISTS cover_key" in migration
    assert "UNIQUE INDEX IF NOT EXISTS uq_wellbeing_ebooks_object_key" in migration
    assert "DROP" not in migration.upper()
    assert "TRUNCATE" not in migration.upper()
    assert "DELETE" not in migration.upper()

    rows = [
        _row(
            2, "guide-deux", "Guide deux",
            object_key="ebooks/guide-deux/ebook.pdf",
            cover_key="ebooks/guide-deux/cover.png",
        ),
        _row(1, "guide-un", "Guide un"),
    ]
    connection = Connection(rows)
    A.get_conn = lambda: connection
    A.resolve_app_session = lambda token: {"user_id": "u"} if token else None
    os.environ["R2_PUBLIC_BASE_URL"] = "https://cdn.example.test"
    A.app.config["TESTING"] = True
    client = A.app.test_client()

    assert client.get("/api/app/wellbeing-ebooks").status_code == 401
    response = client.get(
        "/api/app/wellbeing-ebooks",
        headers={"Authorization": "Bearer test"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["ebooks"]) == 2
    assert payload["ebooks"][0]["slug"] == "guide-deux"
    assert payload["ebooks"][0]["pdf_url"] == (
        "https://cdn.example.test/ebooks/guide-deux/ebook.pdf"
    )
    assert payload["ebooks"][0]["cover_url"] == (
        "https://cdn.example.test/ebooks/guide-deux/cover.png"
    )
    assert payload["ebooks"][0]["object_key"] == "ebooks/guide-deux/ebook.pdf"
    assert payload["ebooks"][0]["cover_key"] == "ebooks/guide-deux/cover.png"
    assert payload["ebooks"][0]["category"] == "Sommeil"
    assert payload["ebooks"][1]["cover_url"] is None
    assert payload["ebooks"][1]["cover_key"] is None
    assert "R2_SECRET_ACCESS_KEY" not in json.dumps(payload)
    assert "100" not in json.dumps(payload)

    print("RÉSULTAT : migration + auth + catalogue dynamique + payload Flutter OK")


if __name__ == "__main__":
    main()
