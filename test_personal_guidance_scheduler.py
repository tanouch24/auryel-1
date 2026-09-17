"""Tests déterministes du cycle Personal Guidance, sans DB ni FCM réels."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from push_scheduler import DbPushTickStore, PushSchedule


PARIS = ZoneInfo("Europe/Paris")


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.cursor_obj = _Cursor(rows)

    def cursor(self):
        return self.cursor_obj

    def close(self):
        pass


def _jobs(age, advisor="thea", now=None):
    now = now or datetime(2026, 9, 17, 8, 30, tzinfo=timezone.utc)
    activity = now.astimezone(PARIS) - timedelta(days=age, minutes=10)
    store = DbPushTickStore(lambda: _Connection([("u1", advisor, activity)]))
    return store.personal_guidance_jobs(now)


def test_only_j1_j3_j5_are_due_and_cycle_stops():
    assert _jobs(0) == []
    assert len(_jobs(1)) == 1
    assert len(_jobs(3)) == 1
    assert len(_jobs(5)) == 1
    assert _jobs(6) == []


def test_personal_guidance_targets_last_advisor_and_has_stable_period():
    first = _jobs(3, advisor="thea")[0]
    second = _jobs(3, advisor="thea")[0]
    assert first["data"] == {"advisor": "thea"}
    assert first["title"].startswith("Théa")
    assert first["period"] == second["period"]
    assert _jobs(3, advisor="unknown") == []


def test_wellbeing_session_is_tuesday_and_saturday_at_10_paris():
    schedule = PushSchedule()
    tue = datetime(2026, 9, 15, 8, 5, tzinfo=timezone.utc)
    wed = datetime(2026, 9, 16, 8, 5, tzinfo=timezone.utc)
    sat = datetime(2026, 9, 19, 8, 5, tzinfo=timezone.utc)
    assert ("wellbeing_session", "2026-09-15") in schedule.due_categories(tue.astimezone(PARIS))
    assert all(category != "wellbeing_session" for category, _ in schedule.due_categories(wed.astimezone(PARIS)))
    assert ("wellbeing_session", "2026-09-19") in schedule.due_categories(sat.astimezone(PARIS))


def test_guidance_precedes_editorial_thought_for_global_daily_cap():
    due = PushSchedule().due_categories(
        datetime(2026, 9, 17, 6, 35, tzinfo=timezone.utc).astimezone(PARIS)
    )
    assert [category for category, _ in due[:2]] == [
        "personal_guidance", "daily_thought"
    ]
