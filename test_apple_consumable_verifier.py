"""Tests locaux du chemin Apple Extra Hour.

Les appels Apple et la base sont remplacés par des doubles : aucun credential
réel et aucun appel réseau ne sont utilisés.
"""

import datetime
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.modules["psycopg2"] = MagicMock()
for key, value in {
    "SECRET_KEY": "t", "VERIFY_TOKEN": "t", "ADMIN_PASSWORD": "t",
    "CRON_SECRET": "t", "DATABASE_URL": "postgresql://t:t@localhost/t",
    "STRIPE_SK": "t", "STRIPE_WEBHOOK_SECRET": "t", "WHATSAPP_TOKEN": "t",
    "PHONE_NUMBER_ID": "t", "GROQ_API_KEY": "t", "RESEND_API_KEY": "t",
    "META_APP_SECRET": "t", "DAILY_SECRET": "t", "SEO_SECRET": "t",
    "TAROT_MEDIA_UPLOAD_DISABLED": "1",
}.items():
    os.environ.setdefault(key, value)

import auryel_bot as A

NOW = datetime.datetime(2026, 9, 24, 12, tzinfo=datetime.timezone.utc)
TX_ID = "2000000000000001"
PRODUCT = "auryel_extra_hour"
CONFIG = ("issuer", "key", "pem", "com.auryel.auryel", "production", 6499999999)


class FakeClient:
    def get_transaction_info(self, transaction_id):
        assert transaction_id == TX_ID
        return SimpleNamespace(signedTransactionInfo="SIGNED")


class FakeVerifier:
    def __init__(self, **values):
        self.values = values

    def verify_and_decode_signed_transaction(self, signed):
        assert signed == "SIGNED"
        values = {
            "transactionId": TX_ID,
            "productId": PRODUCT,
            "bundleId": "com.auryel.auryel",
            "environment": "Production",
            "originalTransactionId": TX_ID,
            "purchaseDate": int(NOW.timestamp() * 1000),
            "revocationDate": None,
            "type": "Consumable",
        }
        values.update(self.values)
        return SimpleNamespace(**values)


def expect_rejected(values, label):
    with patch.object(A, "_apple_config", return_value=CONFIG), \
         patch.object(A, "_apple_api_client", return_value=FakeClient()), \
         patch.object(A, "_apple_signed_data_verifier",
                      return_value=FakeVerifier(**values)):
        try:
            A._apple_verify_consumable(TX_ID, PRODUCT, now=NOW)
        except A.StoreVerificationError as exc:
            assert exc.code == "invalid_store_receipt", label
        else:
            raise AssertionError(label)


def main():
    with patch.object(A, "_apple_config", return_value=CONFIG), \
         patch.object(A, "_apple_api_client", return_value=FakeClient()), \
         patch.object(A, "_apple_signed_data_verifier", return_value=FakeVerifier()):
        normalized = A._apple_verify_consumable(TX_ID, PRODUCT, now=NOW)
    assert normalized["store"] == "app_store"
    assert normalized["product_id"] == PRODUCT
    assert normalized["purchase_key"] == TX_ID
    assert normalized["purchased_at"] == NOW
    print("PASS valid Apple Extra Hour -> normalized transaction")

    expect_rejected({"productId": "wrong_product"}, "wrong product rejected")
    print("PASS wrong product rejected")
    expect_rejected({"type": "Auto-Renewable Subscription"}, "wrong type rejected")
    print("PASS non-consumable transaction rejected")
    expect_rejected({"originalTransactionId": "1999999999999999"},
                    "restored transaction rejected")
    print("PASS restored transaction rejected")
    expect_rejected({"revocationDate": int(NOW.timestamp() * 1000)},
                    "revoked transaction rejected")
    print("PASS revoked transaction rejected")
    expect_rejected({"environment": "Sandbox"}, "environment mismatch rejected")
    print("PASS environment mismatch rejected")
    expect_rejected({"bundleId": "com.other.app"}, "bundle mismatch rejected")
    print("PASS bundle mismatch rejected")

    with patch.dict(os.environ, {
        "APPLE_ASC_ISSUER_ID": "", "APPLE_ASC_KEY_ID": "",
        "APPLE_ASC_PRIVATE_KEY": "", "APPLE_BUNDLE_ID": "",
    }, clear=False):
        try:
            A._apple_verify_consumable(TX_ID, PRODUCT, now=NOW)
        except A.StoreVerificationError as exc:
            assert exc.code == "verification_not_configured"
        else:
            raise AssertionError("missing Apple credentials must fail closed")
    print("PASS missing Apple credentials -> fail closed")


if __name__ == "__main__":
    main()
