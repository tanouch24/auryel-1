"""Contract tests for the retired historical Stars award primitive.

The active product uses Rewarded V1. Historical Stars tables remain available
for audit/migration purposes, but ``award_stars`` must not create a wallet,
write a reward transaction, or mutate a daily claim.
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()
for _key, _value in {
    "SECRET_KEY": "test-secret-key", "VERIFY_TOKEN": "test",
    "ADMIN_PASSWORD": "test", "DATABASE_URL": "postgresql://test:test@localhost/test",
    "STRIPE_SK": "sk_test_placeholder", "STRIPE_WEBHOOK_SECRET": "whsec_placeholder",
    "WHATSAPP_TOKEN": "test", "PHONE_NUMBER_ID": "test", "GROQ_API_KEY": "test",
    "RESEND_API_KEY": "test", "META_APP_SECRET": "test", "DAILY_SECRET": "test",
    "SEO_SECRET": "test",
}.items():
    os.environ.setdefault(_key, _value)

import auryel_bot as A


UID = "11111111-1111-4111-8111-111111111111"
UNKNOWN_UID = "99999999-9999-4999-8999-999999999999"
NOW = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)

DB = {
    "accounts": {UID: {"user_id": UID, "deleted_at": None}},
    "reward_wallet": {}, "reward_transactions": [], "daily_action_claims": [],
}


class FakeCursor:
    def __init__(self):
        self._result = None

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "FROM accounts WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE" in normalized:
            row = DB["accounts"].get(params[0])
            self._result = (row["user_id"],) if row else None
            return
        raise AssertionError(f"Unexpected SQL for retired award_stars: {normalized}")

    def fetchone(self):
        return self._result

    def close(self):
        pass


class FakeConnection:
    def cursor(self):
        return FakeCursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


A.get_conn = lambda: FakeConnection()


def check(condition, label):
    if not condition:
        raise AssertionError(label)
    print(f"OK  {label}")


def reset_state():
    DB["reward_wallet"].clear()
    DB["reward_transactions"].clear()
    DB["daily_action_claims"].clear()


reset_state()
first = A.award_stars(
    UID, "meditation_completed",
    idempotency_key="meditation_completed:u1:2026-09-09", now=NOW,
)
check(first == {
    "awarded": False, "reason": "stars_economy_retired",
    "stars_awarded": 0, "new_balance": None,
}, "known account returns the retired Stars contract")
check(not DB["reward_wallet"], "retired award does not create a wallet")
check(not DB["reward_transactions"], "retired award does not write a transaction")
check(not DB["daily_action_claims"], "retired award does not write a daily claim")

second = A.award_stars(
    UID, "meditation_completed",
    idempotency_key="meditation_completed:u1:retry", now=NOW,
)
check(second == first, "repeated retired award remains side-effect free")

unknown = A.award_stars(
    UNKNOWN_UID, "meditation_completed", idempotency_key="unknown-account", now=NOW,
)
check(unknown == {
    "awarded": False, "reason": "unknown_account",
    "stars_awarded": 0, "new_balance": 0,
}, "unknown account is rejected before the retired award contract")

print("RESULTAT : 7/7 tests Rewarded/Stars contract passés")
