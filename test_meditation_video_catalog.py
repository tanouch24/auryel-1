"""Tests ciblés du catalogue MP4 Méditations R2.

Tests hors réseau : le client R2 et la DB sont injectés. Aucun objet réel et
aucun credential ne sont utilisés.
"""
import inspect
import os
from datetime import datetime, timezone

for _key, _value in {
    "SECRET_KEY": "test-secret-key",
    "ADMIN_PASSWORD": "test",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
}.items():
    os.environ.setdefault(_key, _value)

import r2_media_sync as R
import auryel_bot as A


class Cursor:
    def __init__(self, db):
        self.db = db
        self.one = None
        self.rows = []
        self.rowcount = -1

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        self.one = None
        self.rows = []
        self.rowcount = -1
        if q.startswith("SELECT r2_object_key FROM"):
            table = q.split("FROM ", 1)[1].split(" ", 1)[0]
            self.rows = [(x["r2_object_key"],) for x in self.db.rows.get(table, [])
                         if x.get("r2_object_key")]
        elif q.startswith("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM"):
            table = q.split("FROM ", 1)[1]
            self.one = (max([x["sort_order"] for x in self.db.rows.get(table, [])] or [0]) + 1,)
        elif q.startswith("SELECT 1 FROM"):
            self.one = None
        elif q.startswith("SELECT id FROM"):
            self.one = None
        elif q.startswith("INSERT INTO meditation_video_catalog"):
            p = tuple(params)
            self.db.rows["meditation_video_catalog"].append({
                "id": p[0], "slug": p[1], "title": p[2], "sort_order": p[7],
                "r2_object_key": p[8],
            })
            self.one = (p[0],)
        elif q.startswith("SELECT pg_try_advisory_lock"):
            self.one = (True,)
        elif q.startswith("SELECT pg_advisory_unlock"):
            self.one = (True,)
        elif q.startswith("INSERT INTO r2_sync_state"):
            pass
        else:
            raise AssertionError(q)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class Conn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return Cursor(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class DB:
    def __init__(self):
        self.rows = {
            "meditation_catalog": [],
            "relaxation_video_catalog": [],
            "meditation_video_catalog": [],
        }


class R2:
    def list_objects(self, prefix):
        if prefix == R.MEDITATION_VIDEO_PREFIX:
            yield {"key": "meditations/ocean.mp4", "size": 100}
            yield {"key": "meditations/second.mp4", "size": 100}


def main():
    assert R.MEDITATION_VIDEO_PREFIX == "meditations/"
    assert R.VIDEO_PREFIX == "relaxation-videos/"
    assert R.derive_video("meditations/ocean.mp4")[1] == "Ocean"

    db = DB()
    sync = R.R2MediaCatalogSync(
        lambda: Conn(db), R2(), public_base_url="https://cdn.example",
        now_fn=lambda: datetime(2026, 9, 16, tzinfo=timezone.utc),
    )
    result = sync.run()
    assert result["meditation_video_objects"] == 2
    assert result["new_meditation_videos"] == 2
    assert len(db.rows["meditation_video_catalog"]) == 2
    assert all(x["r2_object_key"].startswith("meditations/")
               for x in db.rows["meditation_video_catalog"])
    assert not db.rows["relaxation_video_catalog"]

    class EndpointCursor:
        def execute(self, sql, params=()):
            self.rows = [
                ("id-a", "a", "A", "", "calm", None,
                 "https://cdn.example/a.mp4", None, 1, None,
                 datetime(2026, 9, 16, tzinfo=timezone.utc), 1,
                 "meditations/a.mp4"),
                ("id-w", "wake", "Wake", "", "", None,
                 "https://cdn.example/w.mp4", None, 2, None,
                 datetime(2026, 9, 16, tzinfo=timezone.utc), 1,
                 "wake-videos/pilot.mp4"),
            ]

        def fetchall(self):
            return [row for row in self.rows
                    if row[-1].startswith("meditations/")]

        def close(self):
            pass

    class EndpointConn:
        def cursor(self):
            return EndpointCursor()

        def close(self):
            pass

    A.get_conn = lambda: EndpointConn()
    A.resolve_app_session = lambda token: {"user_id": "u"} if token else None
    A.app.config["TESTING"] = True
    response = A.app.test_client().get(
        "/api/app/content/meditations?media=video",
        headers={"Authorization": "Bearer test"},
    )
    body = response.get_json()
    assert response.status_code == 200
    assert [x["object_key"] for x in body["meditation_videos"]] == [
        "meditations/a.mp4"
    ]
    assert A.app.test_client().get(
        "/api/app/content/meditations?media=video"
    ).status_code == 401

    migration = open("migrations/035_meditation_video_catalog.sql", encoding="utf-8").read()
    assert "CREATE TABLE IF NOT EXISTS meditation_video_catalog" in migration
    assert "DROP TABLE" not in migration

    source = inspect.getsource(A.api_content_meditations)
    helper = inspect.getsource(A._meditation_video_catalog_active_rows)
    assert 'request.args.get("media") == "video"' in source
    assert "r2_object_key LIKE 'meditations/%'" in helper
    print("RÉSULTAT : 12 ok / 0 ko")


if __name__ == "__main__":
    main()
