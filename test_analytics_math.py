"""Deterministic QA fixtures for the documented analytics definitions."""

from datetime import date, datetime, timedelta, timezone


def active_count(events, start, end):
    return len({e["account_id"] for e in events if start <= e["at"] < end})


def retention(cohort, events, days):
    retained = 0
    for account_id, signup in cohort.items():
        target = signup + timedelta(days=days)
        if any(e["account_id"] == account_id and e["at"].date() == target for e in events):
            retained += 1
    return retained / len(cohort) if cohort else None


def funnel(events):
    names = ("account_created", "onboarding_completed", "free_consultation_started", "free_consultation_used", "paywall_viewed", "premium_purchased")
    counts = {name: len({e["account_id"] for e in events if e["name"] == name}) for name in names}
    result = []
    first = None
    previous = None
    for name in names:
        first = counts[name] if first is None else first
        result.append((name, counts[name], None if not previous else counts[name] / previous, None if not first else counts[name] / first))
        previous = counts[name]
    return result


def test_activity_deduplicates_accounts_and_respects_boundaries():
    day = datetime(2026, 9, 1, tzinfo=timezone.utc)
    events = [
        {"account_id": "a", "at": day + timedelta(hours=1)},
        {"account_id": "a", "at": day + timedelta(hours=2)},
        {"account_id": "b", "at": day + timedelta(days=1)},
    ]
    assert active_count(events, day, day + timedelta(days=1)) == 1
    assert active_count(events, day, day + timedelta(days=2)) == 2


def test_retention_exact_day_and_multiple_events():
    cohort = {"a": date(2026, 9, 1), "b": date(2026, 9, 1), "c": date(2026, 9, 1)}
    events = [
        {"account_id": "a", "at": datetime(2026, 9, 2, 1, tzinfo=timezone.utc)},
        {"account_id": "a", "at": datetime(2026, 9, 8, 1, tzinfo=timezone.utc)},
        {"account_id": "b", "at": datetime(2026, 9, 9, 1, tzinfo=timezone.utc)},
    ]
    assert retention(cohort, events, 1) == 1 / 3
    assert retention(cohort, events, 7) == 1 / 3
    assert retention(cohort, events, 30) == 0


def test_funnel_handles_duplicates_missing_steps_and_zero_denominators():
    events = [
        {"account_id": "a", "name": "account_created"},
        {"account_id": "a", "name": "account_created"},
        {"account_id": "a", "name": "onboarding_completed"},
        {"account_id": "b", "name": "account_created"},
    ]
    result = funnel(events)
    assert result[0] == ("account_created", 2, None, 1.0)
    assert result[1] == ("onboarding_completed", 1, 0.5, 0.5)
    assert result[2][2] == 0.0


if __name__ == "__main__":
    test_activity_deduplicates_accounts_and_respects_boundaries()
    test_retention_exact_day_and_multiple_events()
    test_funnel_handles_duplicates_missing_steps_and_zero_denominators()
    print("analytics math tests: PASS")
