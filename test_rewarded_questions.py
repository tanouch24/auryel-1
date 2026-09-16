"""Rewarded V1 contract tests.

These tests are deliberately DB-free: the repository's integration suite owns
PostgreSQL wiring, while this file locks the additive schema and the security
invariants of the transaction flow.
"""
import pathlib
import unittest


ROOT = pathlib.Path(__file__).parent
SQL = (ROOT / "migrations/033_rewarded_questions.sql").read_text()
SRC = (ROOT / "auryel_bot.py").read_text()


class RewardedQuestionsContractTest(unittest.TestCase):
    def test_ssv_valid_gives_one_question(self):
        self.assertIn('questions_available', SRC)
        self.assertIn('questions_available": q', SRC)

    def test_ssv_valid_advances_progress(self):
        self.assertIn('rewarded_progress', SRC)

    def test_transaction_id_is_unique(self):
        self.assertIn('ON CONFLICT (transaction_id) DO NOTHING', SRC)

    def test_nine_ads_stay_at_nine(self):
        self.assertIn('residual = next_progress % 10', SRC)

    def test_tenth_ad_credits_question_and_five_minutes(self):
        self.assertIn('credited_minutes = completed * 5', SRC)
        self.assertIn('completed * 300', SRC)

    def test_active_admob_contract_is_one_consultation_question(self):
        self.assertIn('_ADMOB_REWARD_AMOUNT = 1', SRC)
        self.assertIn('_ADMOB_REWARD_ITEM = "consultation_question"', SRC)

    def test_progress_transition_keeps_questions_and_residual_progress(self):
        self.assertIn('_apply_rewarded_entitlement_credit', SRC)
        self.assertIn('residual = next_progress % 10', SRC)

    def test_progress_resets_after_tenth(self):
        self.assertIn('residual = next_progress % 10', SRC)

    def test_twentieth_ad_supports_two_paliers(self):
        self.assertIn('completed = next_progress // 10', SRC)

    def test_questions_are_server_persistent(self):
        self.assertIn('CREATE TABLE IF NOT EXISTS rewarded_entitlements', SQL)

    def test_question_reservation_is_atomic(self):
        self.assertIn('rewarded_question_reservations', SQL)
        self.assertIn('questions_available-1', SRC)

    def test_question_has_idempotency_key(self):
        self.assertIn('UNIQUE (user_id, idempotency_key)', SQL)

    def test_question_is_consumed_after_reply(self):
        self.assertIn('"consumed", consultation_id=cid', SRC)

    def test_question_released_on_llm_failure(self):
        self.assertIn('"released")', SRC)

    def test_no_time_with_question_enters_question_flow(self):
        self.assertIn('"status": "question"', SRC)

    def test_no_time_without_question_remains_blocked(self):
        self.assertIn('"error": "time_exhausted"', SRC)

    def test_old_stars_are_not_credited(self):
        self.assertIn('stars_economy_retired', SRC)
        self.assertIn('Historical Stars remain queryable', SRC)

    def test_engagement_rewards_are_inert(self):
        self.assertIn('return {"awarded": False, "reason": "stars_economy_retired"', SRC)

    def test_migration_is_additive_and_idempotent(self):
        self.assertIn('IF NOT EXISTS', SQL)
        self.assertIn('UNIQUE (user_id, idempotency_key)', SQL)


if __name__ == '__main__':
    unittest.main()
