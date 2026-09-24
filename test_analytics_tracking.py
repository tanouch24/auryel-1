"""Focused, dependency-light tests for first-party analytics boundaries."""

from datetime import datetime, timezone

from analytics_tracking import _clean_properties, record_analytics_event, record_ai_usage


class Cursor:
    def __init__(self):
        self.rowcount = 1
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))

    def fetchone(self):
        return None


class Conn:
    def __init__(self):
        self.cur = Cursor()
        self.committed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True

    def close(self):
        pass


def test_properties_are_bounded_and_private_content_is_rejected():
    assert _clean_properties({"content_type": "ebook", "duration_seconds": 12})["duration_seconds"] == 12
    for key in ("message", "prompt", "api_key", "fcm_token", "signed_url"):
        try:
            _clean_properties({key: "secret"})
        except ValueError:
            pass
        else:
            raise AssertionError(key)


def test_event_insert_is_idempotent_and_fail_safe():
    conn = Conn()
    assert record_analytics_event(lambda: conn, "secret", event_name="app_opened", account_id="u1", idempotency_key="one")
    assert conn.committed
    conn.cur.rowcount = 0
    assert not record_analytics_event(lambda: conn, "secret", event_name="app_opened", account_id="u1", idempotency_key="one")
    assert not record_analytics_event(lambda: (_ for _ in ()).throw(RuntimeError("db unavailable")), "secret", event_name="app_opened")


def test_ai_usage_does_not_store_prompt_or_response_and_unconfigured_cost_is_safe():
    conn = Conn()
    assert record_ai_usage(
        lambda: conn,
        request_id="req-1",
        provider="openai",
        model="model",
        usage={"input_tokens": 2, "output_tokens": 3},
        occurred_at=datetime.now(timezone.utc),
    )
    sql = " ".join(query for query, _ in conn.cur.queries)
    assert "prompt" not in sql.lower()
    assert "response" not in sql.lower()


if __name__ == "__main__":
    test_properties_are_bounded_and_private_content_is_rejected()
    test_event_insert_is_idempotent_and_fail_safe()
    test_ai_usage_does_not_store_prompt_or_response_and_unconfigured_cost_is_safe()
    print("analytics tracking tests: PASS")
