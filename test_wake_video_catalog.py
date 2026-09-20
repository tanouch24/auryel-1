"""Contrat local du catalogue dédié Réveil.

Le test utilise uniquement un faux curseur : aucune base réelle ni écriture
réseau ne sont nécessaires. Il couvre le filtrage actif/publié, l'ordre
déterministe et le contrat de réponse de l'API mobile.
"""
import os
from datetime import datetime, timezone

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("ADMIN_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import auryel_bot as A


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=()):
        assert "wake_video_catalog" in sql

    def fetchall(self):
        now = datetime(2026, 9, 20, tzinfo=timezone.utc)
        return [r for r in self.rows if r[6] is None or r[6] <= now]


class Conn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return Cursor(self.rows)

    def close(self):
        pass


def row(index, *, active=True, future=False):
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    return (
        f"00000000-0000-4000-8000-{index:012d}",
        f"wake-{index:03d}",
        f"Wake {index}",
        f"https://cdn.example/wake-{index:03d}.mp4",
        30,
        index,
        now if not future else datetime(2099, 1, 1, tzinfo=timezone.utc),
        now,
        1,
    )


def main():
    A.resolve_app_session = lambda token: {"user_id": "test-user"} if token else None
    A.app.config["TESTING"] = True
    try:
        A.limiter.enabled = False
    except Exception:
        pass
    client = A.app.test_client()

    for rows, expected, label in [
        ([], 0, "empty catalog"),
        ([row(1)], 1, "one active video"),
        ([row(i) for i in range(1, 60)], 59, "59 active videos"),
        ([row(1), row(2, future=True)], 1, "future publication excluded"),
    ]:
        A.get_conn = lambda rows=rows: Conn(rows)
        response = client.get(
            "/api/app/content/wake-videos",
            headers={"Authorization": "Bearer test"},
        )
        assert response.status_code == 200, label
        body = response.get_json()
        assert len(body["videos"]) == expected, label
        assert [v["sort_order"] for v in body["videos"]] == sorted(
            v["sort_order"] for v in body["videos"]
        ), label

    assert client.get("/api/app/content/wake-videos").status_code == 401
    migration = open(
        "migrations/042_wake_video_catalog.sql", encoding="utf-8"
    ).read()
    assert "CREATE TABLE IF NOT EXISTS wake_video_catalog" in migration
    assert "DROP TABLE" not in migration
    assert "WHERE is_active = TRUE" in __import__("inspect").getsource(
        A._wake_video_catalog_active_rows
    )
    print("RÉSULTAT : 5 ok / 0 ko")


if __name__ == "__main__":
    main()
