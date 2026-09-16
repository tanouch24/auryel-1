"""Tests ciblés du catalogue Exercices Bien-être V1.

Tests locaux : la DB est simulée, aucun accès réseau ou compte utilisateur.
"""
import json
import os
import re
from datetime import datetime, timezone
from unittest.mock import MagicMock

import sys
sys.modules["psycopg2"] = MagicMock()
for _key, _value in {
    "SECRET_KEY": "test-secret-key",
    "ADMIN_PASSWORD": "test",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
}.items():
    os.environ.setdefault(_key, _value)

import auryel_bot as A


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.result = []

    def execute(self, sql, params=()):
        query = " ".join(sql.split())
        if query.startswith("SELECT id, slug, title, category, description"):
            category = params[0] if params else None
            self.result = [r for r in self.rows if category is None or r[3] == category]
        else:
            raise AssertionError(f"SQL non géré : {query}")

    def fetchall(self):
        return self.result

    def close(self):
        pass


class Connection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return Cursor(self.rows)

    def close(self):
        pass


def main():
    os.environ["R2_PUBLIC_BASE_URL"] = "https://r2.example.test/public"
    migration = open(
        "migrations/036_exercise_catalog.sql", encoding="utf-8"
    ).read()
    assert "CREATE TABLE IF NOT EXISTS exercise_catalog" in migration
    assert re.search(r"steps\s+JSONB\s+NOT NULL", migration)
    assert "CHECK (duration_seconds > 0)" in migration
    assert migration.count("::uuid,") == 50
    slugs = re.findall(r"::uuid, '([^']+)'", migration)
    assert len(slugs) == 50
    assert all(
        A._exercise_image_url(slug).startswith(
            "https://r2.example.test/public/exercise-images/"
        )
        and A._exercise_image_url(slug).endswith("_01.webp")
        for slug in slugs
    )
    for category in ("breathing", "relaxation", "stretching", "mobility", "sleep"):
        assert migration.count(f"'{category}'") == 10
    assert "tarot" not in migration.lower()
    assert "reward" not in migration.lower()
    assert not re.search(r"\b(guérit|traite|soigne|élimine l.anxiété)\b", migration, re.I)
    assert "jambes-légères-au-lit_01.webp" not in migration
    assert "jambes-legères-au-lit" in migration
    init_source = open("auryel_bot.py", encoding="utf-8").read()
    assert '"036_exercise_catalog.sql"' in init_source
    assert '@app.route("/api/app/content/exercises", methods=["GET"])' in init_source

    now = datetime(2026, 9, 16, tzinfo=timezone.utc)
    rows = [
        ("id-1", "souffle", "Souffle", "breathing", "Desc", 120,
         "débutant", [{"order": 1, "title": "Observer", "instruction": "Respire", "seconds": 60}],
         "", 1, 1, now),
        ("id-2", "pause", "Pause", "relaxation", "Desc", 120,
         "débutant", [{"order": 1, "title": "Observer", "instruction": "Relâche", "seconds": 60}],
         "", 2, 1, now),
        ("id-3", "inactive", "Inactive", "mobility", "Desc", 120,
         "débutant", [], "", 3, 1, now),
    ]
    A.get_conn = lambda: Connection(rows[:2])
    A.resolve_app_session = lambda token: {"user_id": "u"} if token else None
    A.app.config["TESTING"] = True
    client = A.app.test_client()
    headers = {"Authorization": "Bearer test"}

    response = client.get("/api/app/content/exercises", headers=headers)
    body = response.get_json()
    assert response.status_code == 200
    assert [x["slug"] for x in body["exercises"]] == ["souffle", "pause"]
    assert body["exercises"][0]["steps"][0]["seconds"] == 60
    assert body["exercises"][0]["image_url"] == (
        "https://r2.example.test/public/exercise-images/souffle_01.webp"
    )
    assert client.get(
        "/api/app/content/exercises?category=breathing", headers=headers
    ).get_json()["exercises"][0]["category"] == "breathing"
    unicode_rows = [
        ("id-unicode", "jambes-legères-au-lit", "Jambes légères au lit", "sleep", "Desc", 120,
         "débutant", [{"order": 1, "title": "Observer", "instruction": "Respire", "seconds": 60}],
         "", 42, 1, now),
    ]
    A.get_conn = lambda: Connection(unicode_rows)
    unicode_body = client.get(
        "/api/app/content/exercises", headers=headers
    ).get_json()
    assert unicode_body["exercises"][0]["image_url"].endswith(
        "exercise-images/jambes-leg%C3%A8res-au-lit_01.webp"
    )
    assert client.get(
        "/api/app/content/exercises?category=unknown", headers=headers
    ).status_code == 400
    assert client.get("/api/app/content/exercises").status_code == 401
    assert all(
        "wake" not in x["slug"] and "meditation" not in x["slug"]
        for x in body["exercises"]
    )
    print("RÉSULTAT : 13 ok / 0 ko")


if __name__ == "__main__":
    main()
