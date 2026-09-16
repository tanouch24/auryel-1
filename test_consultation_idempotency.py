"""Contrats locaux du lot 2 : une intention de message = un résultat."""
import importlib
import os
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test")
os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("ADMIN_PASSWORD", "test")
os.environ.setdefault("TAROT_MEDIA_UPLOAD_DISABLED", "1")

A = importlib.import_module("auryel_bot")


class _Cursor:
    def __init__(self, db):
        self.db = db
        self.row = None

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        if sql.startswith("INSERT INTO consultation_message_requests"):
            key = (str(params[0]), str(params[1]))
            with self.db.lock:
                inserted = key not in self.db.rows
                self.db.rows.setdefault(key, {
                    "message_hash": params[2],
                    "status": "processing",
                    "payload": None,
                })
            self.row = (params[0],) if inserted else None
        elif sql.startswith("SELECT message_hash, status, response_payload"):
            key = (str(params[0]), str(params[1]))
            with self.db.lock:
                item = self.db.rows.get(key)
                self.row = ((item["message_hash"], item["status"], item["payload"])
                            if item else None)
        elif sql.startswith("UPDATE consultation_message_requests SET status='processing'"):
            key = (str(params[0]), str(params[1]))
            self.db.rows[key]["status"] = "processing"
        elif sql.startswith("UPDATE consultation_message_requests SET status='completed'"):
            key = (str(params[1]), str(params[2]))
            self.db.rows[key]["status"] = "completed"
            self.db.rows[key]["payload"] = A._json.loads(params[0])
        elif sql.startswith("UPDATE consultation_message_requests SET status='failed'"):
            key = (str(params[0]), str(params[1]))
            self.db.rows[key]["status"] = "failed"
            self.db.rows[key]["payload"] = None
        else:
            raise AssertionError(f"SQL inattendu: {sql}")

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return _Cursor(self.db)

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


class _Db:
    def __init__(self):
        self.rows = {}
        self.lock = threading.RLock()


class ConsultationIdempotencyTest(unittest.TestCase):
    def setUp(self):
        self.db = _Db()
        self.conn = patch.object(A, "get_conn", side_effect=lambda: _Conn(self.db))
        self.conn.start()

    def tearDown(self):
        self.conn.stop()

    def test_completed_rejeu_ne_recree_pas_l_intention(self):
        first = A._claim_consultation_message_request("u", "k", "bonjour")
        self.assertEqual(first["status"], "claimed")
        payload = {"reply": "Je suis là", "message_id": "42"}
        A._finish_consultation_message_request("u", "k", payload)
        replay = A._claim_consultation_message_request("u", "k", "bonjour")
        self.assertEqual(replay, {"status": "completed", "payload": payload})

    def test_concurrent_processing_est_refuse_sans_second_llm(self):
        results = []

        def claim():
            results.append(A._claim_consultation_message_request("u", "k", "bonjour"))

        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(r["status"] for r in results), ["claimed", "processing"])

    def test_cle_differente_ou_message_different_ne_partage_pas_le_resultat(self):
        self.assertEqual(
            A._claim_consultation_message_request("u", "k1", "bonjour")["status"],
            "claimed",
        )
        self.assertEqual(
            A._claim_consultation_message_request("u", "k2", "bonjour")["status"],
            "claimed",
        )
        self.assertEqual(
            A._claim_consultation_message_request("u", "k1", "autre")["status"],
            "conflict",
        )

    def test_echec_est_rejouable_avec_la_meme_cle(self):
        A._claim_consultation_message_request("u", "k", "bonjour")
        A._fail_consultation_message_request("u", "k")
        self.assertEqual(
            A._claim_consultation_message_request("u", "k", "bonjour")["status"],
            "claimed",
        )

    def test_route_utilise_le_resultat_stocke_et_code_metier_quota(self):
        source = __import__("inspect").getsource(A.api_consultation_message)
        self.assertIn("_claim_consultation_message_request", source)
        self.assertIn("_finish_consultation_message_request", source)
        self.assertIn('"consultation_credit_exhausted"', source)
        self.assertIn('"consultation_request_processing"', source)


if __name__ == "__main__":
    unittest.main()
