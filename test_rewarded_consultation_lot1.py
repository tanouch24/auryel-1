"""Focused Lot 1 tests for the server-owned Rewarded Consultation contract."""

import os

os.environ.setdefault("TAROT_MEDIA_UPLOAD_DISABLED", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import auryel_bot as A


def test_seventeen_callbacks_keep_seventeen_questions_and_residual_progress():
    state = {"questions_available": 0, "progress": 0,
             "total_rewarded": 0, "minutes_awarded": 0}
    for _ in range(17):
        state = A._apply_rewarded_entitlement_credit(
            state["questions_available"], state["progress"],
            state["total_rewarded"], state["minutes_awarded"])
    assert state["questions_available"] == 17
    assert state["total_rewarded"] == 17
    assert state["progress"] == 7
    assert state["minutes_awarded"] == 5


def test_twenty_callbacks_keep_questions_and_credit_two_time_bonuses():
    state = {"questions_available": 0, "progress": 0,
             "total_rewarded": 0, "minutes_awarded": 0}
    for _ in range(20):
        state = A._apply_rewarded_entitlement_credit(
            state["questions_available"], state["progress"],
            state["total_rewarded"], state["minutes_awarded"])
    assert state["questions_available"] == 20
    assert state["progress"] == 0
    assert state["total_rewarded"] == 20
    assert state["minutes_awarded"] == 10


def test_active_ssv_contract_is_not_stars():
    assert A._ADMOB_REWARD_AMOUNT == 1
    assert A._ADMOB_REWARD_ITEM == "consultation_question"
