"""Contrat du catalogue vidéo Méditations sur la base main."""
import inspect
import os
from datetime import datetime, timezone

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("ADMIN_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import auryel_bot as A
import r2_media_sync as R


class _Cursor:
    def execute(self, sql, params=()):
        self.one = None
        self.rows = []
        if "pg_try_advisory_lock" in sql:
            self.one = (True,)
        elif "COALESCE(MAX(sort_order)" in sql:
            self.one = (1,)
        elif "SELECT 1 FROM" in sql:
            self.one = None

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


class _Conn:
    def cursor(self):
        return _Cursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _R2:
    class config:
        public_base_url = "https://cdn.example"

    def list_objects(self, prefix):
        if prefix == "meditations/":
            yield {"key": "meditations/ocean.mp4", "size": 100}
            yield {"key": "meditations/rain.mp4", "size": 100}
            yield {"key": "meditations/readme.txt", "size": 10}
        elif prefix == "relaxation-videos/":
            yield {"key": "relaxation-videos/ambient.mp4", "size": 100}


def main():
    assert R.MEDITATION_VIDEO_PREFIX == "meditations/"
    assert R.VIDEO_PREFIX == "relaxation-videos/"
    sync = R.R2MediaCatalogSync(
        lambda: _Conn(), _R2(), public_base_url="https://cdn.example",
        now_fn=lambda: datetime(2026, 9, 16, tzinfo=timezone.utc),
    )
    result = sync.run(dry_run=True)
    assert result["meditation_video_objects"] == 2
    assert result["new_meditation_videos"] == 2
    assert result["video_objects"] == 1

    class EndpointCursor:
        def execute(self, sql, params=()):
            self.rows = [
                ("id-a", "a", "A", "", "calm", None,
                 "https://cdn.example/a.mp4", None, 1, None,
                 datetime(2026, 9, 16, tzinfo=timezone.utc), 1,
                 "meditations/a.mp4"),
                ("id-w", "w", "Wake", "", "calm", None,
                 "https://cdn.example/w.mp4", None, 2, None,
                 datetime(2026, 9, 16, tzinfo=timezone.utc), 1,
                 "wake-videos/pilot.mp4"),
            ]

        def fetchall(self):
            return [r for r in self.rows if r[-1].startswith("meditations/")]

    class EndpointConn:
        def cursor(self):
            return EndpointCursor()

        def close(self):
            pass

    A.get_conn = lambda: EndpointConn()
    A.resolve_app_session = lambda token: {"user_id": "u"} if token else None
    client = A.app.test_client()
    response = client.get(
        "/api/app/content/meditations?media=video",
        headers={"Authorization": "Bearer test"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert [x["object_key"] for x in body["meditation_videos"]] == [
        "meditations/a.mp4"
    ]
    assert client.get("/api/app/content/meditations?media=video").status_code == 401

    migration = open("migrations/035_meditation_video_catalog.sql", encoding="utf-8").read()
    assert "CREATE TABLE IF NOT EXISTS meditation_video_catalog" in migration
    assert "DROP TABLE" not in migration
    assert "request.args.get(\"media\") == \"video\"" in inspect.getsource(
        A.api_content_meditations
    )
    print("RÉSULTAT : 10 ok / 0 ko")


if __name__ == "__main__":
    main()
