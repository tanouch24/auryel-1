import base64
import json
import unittest
from unittest.mock import Mock

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

import admob_ssv


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class AdmobSsvVerifierTest(unittest.TestCase):
    def setUp(self):
        admob_ssv.reset_key_cache_for_tests()
        self.private = ec.generate_private_key(ec.SECP256R1())
        der = self.private.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.fetcher = Mock(return_value=_Response({
            "keys": [{"keyId": 42, "base64": base64.b64encode(der).decode()}]
        }))

    def _callback(self, content):
        signature = self.private.sign(content, ec.ECDSA(hashes.SHA256()))
        encoded = base64.urlsafe_b64encode(signature).rstrip(b"=")
        return content + b"&signature=" + encoded + b"&key_id=42"

    def test_valid_signature_preserves_raw_query(self):
        raw = self._callback(
            b"ad_network=5450213213286189855&ad_unit=unit&reward_amount=6"
            b"&reward_item=stars&timestamp=1&transaction_id=tx&user_id=u"
        )
        result = admob_ssv.verify_callback(raw, fetcher=self.fetcher, now=100)
        self.assertEqual(result["transaction_id"], "tx")
        self.assertEqual(result["key_id"], "42")
        self.assertEqual(result["signed_query"], raw.split(b"&signature=", 1)[0])

    def test_tampering_is_rejected(self):
        raw = self._callback(b"ad_unit=unit&reward_amount=6&transaction_id=tx")
        with self.assertRaises(admob_ssv.SsvError):
            admob_ssv.verify_callback(
                raw.replace(b"reward_amount=6", b"reward_amount=60"),
                fetcher=self.fetcher,
                now=100,
            )

    def test_unknown_key_refreshes_once_then_rejects(self):
        raw = self._callback(b"ad_unit=unit&reward_amount=6&transaction_id=tx")
        self.fetcher.return_value = _Response({"keys": []})
        with self.assertRaises(admob_ssv.SsvError):
            admob_ssv.verify_callback(raw, fetcher=self.fetcher, now=100)
        self.assertEqual(self.fetcher.call_count, 1)

    def test_cache_is_reused_for_24_hours(self):
        raw = self._callback(b"ad_unit=unit&reward_amount=6&transaction_id=tx")
        admob_ssv.verify_callback(raw, fetcher=self.fetcher, now=100)
        admob_ssv.verify_callback(raw, fetcher=self.fetcher, now=100 + 3600)
        self.assertEqual(self.fetcher.call_count, 1)

    def test_duplicate_query_parameter_rejected(self):
        content = b"ad_unit=unit&ad_unit=other&reward_amount=6&transaction_id=tx"
        raw = self._callback(content)
        with self.assertRaises(admob_ssv.SsvError):
            admob_ssv.verify_callback(raw, fetcher=self.fetcher, now=100)

    def test_database_migration_is_additive_and_unique(self):
        with open("migrations/031_admob_rewarded_ssv.sql", encoding="utf-8") as handle:
            migration = handle.read()
        self.assertIn("CREATE TABLE IF NOT EXISTS admob_reward_sessions", migration)
        self.assertIn("CREATE TABLE IF NOT EXISTS admob_reward_events", migration)
        self.assertIn("transaction_id TEXT PRIMARY KEY", migration)
        self.assertNotIn("DROP TABLE", migration.upper())
        self.assertNotIn("TRUNCATE", migration.upper())


if __name__ == "__main__":
    unittest.main()
