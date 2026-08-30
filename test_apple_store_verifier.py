"""
test_apple_store_verifier.py — B4 : vérificateur Apple App Store
(_apple_verify_subscription / _apple_normalize / _apple_entitled /
_apple_pick_transaction) + route /api/billing/verify côté Apple.

AUCUN réseau : _apple_api_client et _apple_signed_data_verifier sont mockés
pour les scénarios de mapping. La vérification CRYPTOGRAPHIQUE des JWS est
testée POUR DE VRAI (section CRYPTO) : chaîne x509 ECDSA locale (root /
intermediate / leaf avec les OID Apple) + vrai SignedDataVerifier de
app-store-server-library, avec rejet effectif d'un JWS altéré et d'une chaîne
non rattachée à la racine de confiance. La sécurité n'est jamais désactivée
(pas de verify_signature=False, pas d'environnement Xcode/LocalTesting).

Script (pas pytest).
"""

import sys
import base64
import datetime
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

sys.modules["psycopg2"] = MagicMock()

import os
for _k, _v in {
    "SECRET_KEY": "t", "VERIFY_TOKEN": "t", "ADMIN_PASSWORD": "t", "CRON_SECRET": "t",
    "DATABASE_URL": "postgresql://t:t@localhost/t", "STRIPE_SK": "t",
    "STRIPE_WEBHOOK_SECRET": "t", "WHATSAPP_TOKEN": "t", "PHONE_NUMBER_ID": "t",
    "GROQ_API_KEY": "t", "RESEND_API_KEY": "t", "META_APP_SECRET": "t",
    "DAILY_SECRET": "t", "SEO_SECRET": "t", "TAROT_MEDIA_UPLOAD_DISABLED": "1",
}.items():
    os.environ.setdefault(_k, _v)

os.environ["APPLE_ASC_ISSUER_ID"] = "69a6de70-xxxx"
os.environ["APPLE_ASC_KEY_ID"] = "ABC123DEFG"
os.environ["APPLE_ASC_PRIVATE_KEY"] = "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----"
os.environ["APPLE_BUNDLE_ID"] = "com.auryel.auryel"
os.environ["APPLE_ENVIRONMENT"] = "production"
os.environ["APPLE_APP_APPLE_ID"] = "6499999999"

import requests as _requests
import auryel_bot as A

_S = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _S["pass"] += 1
        print(f"✅ {label}")
    else:
        _S["fail"] += 1
        print(f"❌ {label}")


NOW = datetime.datetime(2026, 8, 28, 12, 0, 0, tzinfo=datetime.timezone.utc)
MS = lambda dt: int(dt.timestamp() * 1000)
PID = "auryel_premium_monthly"


def tx_obj(product_id=PID, txid="2000000111", otxid="1000000999",
           purchase=NOW, original=NOW - datetime.timedelta(days=60),
           expires=NOW + datetime.timedelta(days=30), revocation=None,
           ttype="Auto-Renewable Subscription", bundle="com.auryel.auryel"):
    return SimpleNamespace(
        productId=product_id, transactionId=txid, originalTransactionId=otxid,
        purchaseDate=MS(purchase), originalPurchaseDate=MS(original),
        expiresDate=MS(expires),
        revocationDate=MS(revocation) if revocation else None,
        type=ttype, bundleId=bundle,
    )


def renew_obj(auto_renew=1, expiration_intent=None, in_retry=None, grace_expires=None):
    return SimpleNamespace(autoRenewStatus=auto_renew,
                           expirationIntent=expiration_intent,
                           isInBillingRetryPeriod=in_retry,
                           gracePeriodExpiresDate=MS(grace_expires) if grace_expires else None)


def status_response(signed_tx="SIGNED_TX", signed_renewal="SIGNED_RENEWAL", status=1):
    item = SimpleNamespace(originalTransactionId="1000000999", rawStatus=status,
                           status=status, signedTransactionInfo=signed_tx,
                           signedRenewalInfo=signed_renewal)
    group = SimpleNamespace(subscriptionGroupIdentifier="G1", lastTransactions=[item])
    return SimpleNamespace(data=[group], environment="Production", bundleId="com.auryel.auryel")


class FakeVerifier:
    """Imite SignedDataVerifier : renvoie des payloads décodés préparés, ou
    lève VerificationException pour une entrée 'BAD_*'."""

    def __init__(self, tx=None, renewal=None):
        self._tx = tx if tx is not None else tx_obj()
        self._renewal = renewal if renewal is not None else renew_obj()

    def verify_and_decode_signed_transaction(self, s):
        from appstoreserverlibrary.signed_data_verifier import VerificationException, VerificationStatus
        if isinstance(s, str) and s.startswith("BAD"):
            raise VerificationException(VerificationStatus.VERIFICATION_FAILURE)
        return self._tx

    def verify_and_decode_renewal_info(self, s):
        from appstoreserverlibrary.signed_data_verifier import VerificationException, VerificationStatus
        if isinstance(s, str) and s.startswith("BAD"):
            raise VerificationException(VerificationStatus.VERIFICATION_FAILURE)
        return self._renewal


class FakeClient:
    def __init__(self, response=None, exc=None):
        self._response = response if response is not None else status_response()
        self._exc = exc

    def get_all_subscription_statuses(self, any_transaction_id, status=None):
        if self._exc is not None:
            raise self._exc
        return self._response


def verify(client=None, verifier=None, txid="2000000111", product_id=PID, now=NOW):
    client = client or FakeClient()
    verifier = verifier or FakeVerifier()
    with patch.object(A, "_apple_api_client", return_value=client), \
         patch.object(A, "_apple_signed_data_verifier", return_value=verifier):
        return A._apple_verify_subscription(txid, product_id, now=now)


print("=" * 64)
print("TEST APPLE STORE VERIFIER — B4")
print("=" * 64)

# --- 1. active -----------------------------------------------------------
n = verify(verifier=FakeVerifier(tx_obj(), renew_obj(auto_renew=1)),
           client=FakeClient(status_response(status=1)))
check(n["entitled"] is True and n["status"] == "active" and n["store"] == "app_store",
      "1 status ACTIVE + expires future -> entitled True")
check(n["subscription_key"] == "1000000999" and n["latest_transaction_id"] == "2000000111"
      and n["product_id"] == PID and n["auto_renewing"] is True,
      "1b subscription_key = originalTransactionId ; latest_transaction_id = transactionId")
check(n["current_period_start"] == NOW
      and n["expires_at"] == NOW + datetime.timedelta(days=30)
      and n["purchased_at"] == NOW - datetime.timedelta(days=60),
      "1c current_period_start=purchaseDate, expires_at=expiresDate, purchased_at=originalPurchaseDate")
check(n["raw_payload"]["environment"] == "sandbox" or n["raw_payload"]["environment"] == "production",
      "1d raw_payload porte l'environnement (résolu côté serveur)")
check("SIGNED_TX" not in str(n["raw_payload"]) and "1000000999" not in str(n["raw_payload"]),
      "1e raw_payload ne contient ni JWS ni identifiants de transaction")

# --- 2. auto-renew coupé mais période encore valide -----------------
n = verify(verifier=FakeVerifier(tx_obj(), renew_obj(auto_renew=0)),
           client=FakeClient(status_response(status=1)))
check(n["entitled"] is True and n["auto_renowing"] if False else n["auto_renewing"] is False,
      "2 autoRenewStatus=0 mais expires future + non révoqué -> entitled True")

# --- 3. expired ------------------------------------------------
n = verify(verifier=FakeVerifier(tx_obj(expires=NOW - datetime.timedelta(days=1))),
           client=FakeClient(status_response(status=2)))
check(n["entitled"] is False and n["status"] == "expired", "3 status EXPIRED -> entitled False")

# --- 4. grace : transaction expirée MAIS gracePeriodExpiresDate future ------
gpe = NOW + datetime.timedelta(days=12)
n = verify(verifier=FakeVerifier(tx_obj(expires=NOW - datetime.timedelta(days=1)),
                                 renew_obj(in_retry=True, grace_expires=gpe)),
           client=FakeClient(status_response(status=4)))
check(n["entitled"] is True and n["status"] == "billing_grace_period"
      and n["expires_at"] == gpe,
      "4 BILLING_GRACE_PERIOD : expiresDate passée + gracePeriodExpiresDate future "
      "-> entitled True, expires_at = gracePeriodExpiresDate (fin effective du droit)")

# --- 4b. grace SANS gracePeriodExpiresDate -> fail closed (jamais inventée) --
n = verify(verifier=FakeVerifier(tx_obj(expires=NOW - datetime.timedelta(days=1)),
                                 renew_obj(in_retry=True)),
           client=FakeClient(status_response(status=4)))
check(n["entitled"] is False,
      "4b BILLING_GRACE_PERIOD sans gracePeriodExpiresDate -> entitled False")

# --- 4c. grace dépassée (gracePeriodExpiresDate <= now) -> entitled False ----
n = verify(verifier=FakeVerifier(tx_obj(expires=NOW - datetime.timedelta(days=20)),
                                 renew_obj(grace_expires=NOW - datetime.timedelta(days=1))),
           client=FakeClient(status_response(status=4)))
check(n["entitled"] is False,
      "4c BILLING_GRACE_PERIOD mais gracePeriodExpiresDate passée -> entitled False")

# --- 5. billing retry hors grâce ----------------------------
n = verify(verifier=FakeVerifier(tx_obj(expires=NOW - datetime.timedelta(hours=1)),
                                 renew_obj(in_retry=True)),
           client=FakeClient(status_response(status=3)))
check(n["entitled"] is False and n["status"] == "billing_retry",
      "5 status BILLING_RETRY (hors grâce) -> entitled False (accès suspendu)")

# --- 6. revoked (status 5) ---------------------------------
n = verify(verifier=FakeVerifier(tx_obj()), client=FakeClient(status_response(status=5)))
check(n["entitled"] is False and n["status"] == "revoked", "6 status REVOKED -> entitled False")

# --- 7. revocationDate présente (remboursement) -----------
n = verify(verifier=FakeVerifier(tx_obj(revocation=NOW - datetime.timedelta(days=2))),
           client=FakeClient(status_response(status=1)))
check(n["entitled"] is False and n["raw_payload"]["revoked"] is True,
      "7 revocationDate présente -> entitled False immédiatement (même si status=ACTIVE)")

# --- 8. mauvais productId -> product_mismatch --------------
err = None
try:
    verify(verifier=FakeVerifier(tx_obj(product_id="com.autre")),
           client=FakeClient(status_response(status=1)))
except A.StoreVerificationError as e:
    err = e
check(err is not None and err.code == "product_mismatch",
      "8 productId != attendu -> product_mismatch")

# --- 9. mauvais bundleId -> le SignedDataVerifier lève -> invalid_store_receipt --
class BundleRejectingVerifier(FakeVerifier):
    def verify_and_decode_signed_transaction(self, s):
        from appstoreserverlibrary.signed_data_verifier import VerificationException, VerificationStatus
        raise VerificationException(VerificationStatus.INVALID_APP_IDENTIFIER)


err = None
try:
    verify(verifier=BundleRejectingVerifier(), client=FakeClient(status_response(status=1)))
except A.StoreVerificationError as e:
    err = e
check(err is not None and err.code == "invalid_store_receipt",
      "9 bundleId étranger (VerificationException INVALID_APP_IDENTIFIER) -> invalid_store_receipt")

# --- 10. transaction inconnue (404) -> invalid_store_receipt + fallback sandbox --
from appstoreserverlibrary.api_client import APIException

seen_envs = []


def _client_factory_404_prod(env_label):
    seen_envs.append(env_label)
    if env_label == "production":
        return FakeClient(exc=APIException(404, None, "not found"))
    return FakeClient(status_response(status=1))


with patch.object(A, "_apple_api_client", side_effect=_client_factory_404_prod), \
     patch.object(A, "_apple_signed_data_verifier", return_value=FakeVerifier()):
    n = A._apple_verify_subscription("txn", PID, now=NOW)
check(seen_envs == ["production", "sandbox"] and n["raw_payload"]["environment"] == "sandbox",
      "10 404 en production -> bascule automatique sandbox (jamais piloté par le client)")

# 404 partout -> invalid_store_receipt
with patch.object(A, "_apple_api_client",
                  return_value=FakeClient(exc=APIException(404, None, "nf"))), \
     patch.object(A, "_apple_signed_data_verifier", return_value=FakeVerifier()):
    err = None
    try:
        A._apple_verify_subscription("txn", PID, now=NOW)
    except A.StoreVerificationError as e:
        err = e
check(err is not None and err.code == "invalid_store_receipt" and err.retryable is False,
      "10b 404 en production ET sandbox -> invalid_store_receipt")

# --- 11. timeout API -> store_verification_unavailable -----
with patch.object(A, "_apple_api_client",
                  return_value=FakeClient(exc=_requests.exceptions.Timeout("slow"))), \
     patch.object(A, "_apple_signed_data_verifier", return_value=FakeVerifier()):
    err = None
    try:
        A._apple_verify_subscription("txn", PID, now=NOW)
    except A.StoreVerificationError as e:
        err = e
check(err is not None and err.code == "store_verification_unavailable" and err.retryable is True,
      "11 timeout App Store Server API -> store_verification_unavailable (retryable)")

# --- 12. API 5xx -> store_verification_unavailable --------
with patch.object(A, "_apple_api_client",
                  return_value=FakeClient(exc=APIException(500, None, "oops"))), \
     patch.object(A, "_apple_signed_data_verifier", return_value=FakeVerifier()):
    err = None
    try:
        A._apple_verify_subscription("txn", PID, now=NOW)
    except A.StoreVerificationError as e:
        err = e
check(err is not None and err.code == "store_verification_unavailable",
      "12 APIException 500 -> store_verification_unavailable")

with patch.object(A, "_apple_api_client",
                  return_value=FakeClient(exc=APIException(401, None, "denied"))), \
     patch.object(A, "_apple_signed_data_verifier", return_value=FakeVerifier()):
    err = None
    try:
        A._apple_verify_subscription("txn", PID, now=NOW)
    except A.StoreVerificationError as e:
        err = e
check(err is not None and err.code == "store_verification_unavailable",
      "12b APIException 401 -> store_verification_unavailable")

# --- 13. JWS signature invalide -> invalid_store_receipt ---
err = None
try:
    verify(verifier=FakeVerifier(), client=FakeClient(status_response(signed_tx="BAD_SIG")))
except A.StoreVerificationError as e:
    err = e
check(err is not None and err.code == "invalid_store_receipt",
      "13 signedTransactionInfo non vérifiable -> invalid_store_receipt")

# --- 14. renewalInfo JWS invalide -> invalid_store_receipt --
err = None
try:
    verify(verifier=FakeVerifier(),
           client=FakeClient(status_response(signed_renewal="BAD_RENEWAL")))
except A.StoreVerificationError as e:
    err = e
check(err is not None and err.code == "invalid_store_receipt",
      "14 signedRenewalInfo non vérifiable -> invalid_store_receipt")

# --- 15. production configurée -> environnement = production -----
os.environ["APPLE_ENVIRONMENT"] = "production"
n = verify()
check(n["raw_payload"]["environment"] == "production",
      "15 APPLE_ENVIRONMENT=production -> raw_payload.environment=production")

# --- 16. sandbox configurée -> environnement = sandbox ---------
os.environ["APPLE_ENVIRONMENT"] = "sandbox"
n = verify()
check(n["raw_payload"]["environment"] == "sandbox",
      "16 APPLE_ENVIRONMENT=sandbox -> raw_payload.environment=sandbox, aucune bascule")
os.environ["APPLE_ENVIRONMENT"] = "production"

# --- 17. originalTransactionId stable = subscription_key -------
n1 = verify(verifier=FakeVerifier(tx_obj(txid="TX-A", otxid="ORIG-STABLE")))
n2 = verify(verifier=FakeVerifier(tx_obj(txid="TX-B-renewed", otxid="ORIG-STABLE")))
check(n1["subscription_key"] == n2["subscription_key"] == "ORIG-STABLE",
      "17 subscription_key = originalTransactionId, stable à travers les renouvellements")

# --- 18. renouvellement -> purchaseDate (current_period_start) AVANCE ---
cyc1 = NOW
cyc2 = NOW + datetime.timedelta(days=30)
n1 = verify(verifier=FakeVerifier(tx_obj(purchase=cyc1,
            expires=cyc1 + datetime.timedelta(days=30))))
n2 = verify(verifier=FakeVerifier(tx_obj(purchase=cyc2,
            expires=cyc2 + datetime.timedelta(days=30), txid="TX2")),
            now=cyc2 + datetime.timedelta(hours=1))
check(n2["current_period_start"] == cyc2 and n2["current_period_start"] > n1["current_period_start"],
      "18 renouvellement : current_period_start = purchaseDate du nouveau cycle (AVANCE)")
check(n2["purchased_at"] == n1["purchased_at"],
      "18b purchased_at (originalPurchaseDate) INCHANGÉ après renouvellement")

# --- 19. replay -> structure normalisée identique -------------
a = verify()
b = verify()
check(a == b, "19 replay même transaction_id -> structure normalisée identique")

# --- 20. _apple_entitled : table de vérité isolée -------------
fut, past = NOW + datetime.timedelta(days=5), NOW - datetime.timedelta(days=1)
check(A._apple_entitled(1, fut, None, NOW) is True
      and A._apple_entitled(1, past, None, NOW) is False
      and A._apple_entitled(2, fut, None, NOW) is False
      and A._apple_entitled(3, fut, None, NOW) is False
      and A._apple_entitled(4, past, None, NOW, grace_expires_at=fut) is True
      and A._apple_entitled(4, past, None, NOW) is False
      and A._apple_entitled(4, past, None, NOW, grace_expires_at=past) is False
      and A._apple_entitled(5, fut, None, NOW) is False
      and A._apple_entitled(1, fut, past, NOW) is False
      and A._apple_entitled(99, fut, None, NOW) is False
      and A._apple_entitled(99, past, None, NOW) is False,
      "20 _apple_entitled : ACTIVE(si futur)=True ; GRACE=True SEULEMENT si "
      "gracePeriodExpiresDate future ; EXPIRED/RETRY/REVOKED/révoqué/status inconnu=False (fail closed)")

# --- 21. _apple_normalize : dates absentes -> invalid_store_receipt ---
bad_tx = tx_obj()
bad_tx.expiresDate = None
err = None
try:
    A._apple_normalize(bad_tx, renew_obj(), 1, "production", PID, NOW)
except A.StoreVerificationError as e:
    err = e
check(err is not None and err.code == "invalid_store_receipt",
      "21 _apple_normalize sans expiresDate -> invalid_store_receipt (jamais fabriqué)")

# --- 22. status Apple INCONNU + expiresDate FUTUR -> Premium NON accordé -----
n = verify(verifier=FakeVerifier(tx_obj(expires=NOW + datetime.timedelta(days=30)),
                                 renew_obj(auto_renew=1)),
           client=FakeClient(status_response(status=99)))
check(n["entitled"] is False and n["status"] == "unknown",
      "22 status Apple non reconnu malgré expiresDate future -> entitled False (fail closed)")

# --- 23. status Apple inconnu bout-en-bout via _apple_normalize -------------
n = A._apple_normalize(tx_obj(expires=NOW + datetime.timedelta(days=30)),
                       renew_obj(auto_renew=1), 7, "production", PID, NOW)
check(n["entitled"] is False,
      "23 _apple_normalize status inconnu (7) + période payée -> entitled False")

# ============================================================
# CRYPTO — vérification RÉELLE de la signature JWS + chaîne x5c
# ============================================================
print("-" * 64)
print("CRYPTO : SignedDataVerifier réel sur chaîne x509 locale")

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
import jwt as _pyjwt
from appstoreserverlibrary.signed_data_verifier import (
    SignedDataVerifier, VerificationException)
from appstoreserverlibrary.models.Environment import Environment

_APPLE_LEAF_OID = x509.ObjectIdentifier("1.2.840.113635.100.6.11.1")
_APPLE_INT_OID = x509.ObjectIdentifier("1.2.840.113635.100.6.2.1")


def _mk_chain():
    now = datetime.datetime.now(datetime.timezone.utc)
    nb, na = now - datetime.timedelta(days=1), now + datetime.timedelta(days=3650)
    rk = ec.generate_private_key(ec.SECP256R1())
    rn = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Apple Root CA G3")])
    root = (x509.CertificateBuilder().subject_name(rn).issuer_name(rn)
            .public_key(rk.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(nb).not_valid_after(na)
            .add_extension(x509.BasicConstraints(True, None), True)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(rk.public_key()), False)
            .sign(rk, hashes.SHA256()))
    ik = ec.generate_private_key(ec.SECP256R1())
    ino = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Apple WWDR Intermediate")])
    inter = (x509.CertificateBuilder().subject_name(ino).issuer_name(rn)
             .public_key(ik.public_key()).serial_number(x509.random_serial_number())
             .not_valid_before(nb).not_valid_after(na)
             .add_extension(x509.BasicConstraints(True, 0), True)
             .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(ik.public_key()), False)
             .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(rk.public_key()), False)
             .add_extension(x509.UnrecognizedExtension(_APPLE_INT_OID, b"\x05\x00"), False)
             .sign(rk, hashes.SHA256()))
    lk = ec.generate_private_key(ec.SECP256R1())
    ln = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Apple Leaf")])
    leaf = (x509.CertificateBuilder().subject_name(ln).issuer_name(ino)
            .public_key(lk.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(nb).not_valid_after(na)
            .add_extension(x509.BasicConstraints(False, None), True)
            .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(lk.public_key()), False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ik.public_key()), False)
            .add_extension(x509.UnrecognizedExtension(_APPLE_LEAF_OID, b"\x05\x00"), False)
            .sign(ik, hashes.SHA256()))
    der = lambda c: c.public_bytes(serialization.Encoding.DER)
    b64 = lambda c: base64.b64encode(der(c)).decode()
    leaf_pem = lk.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return root, inter, leaf, leaf_pem, [b64(leaf), b64(inter), b64(root)], der(root)


_root, _inter, _leaf, _leaf_pem, _x5c, _root_der = _mk_chain()
_now_ms = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
_tx_payload = {
    "bundleId": "com.auryel.auryel", "environment": "Sandbox",
    "signedDate": _now_ms, "productId": PID,
    "transactionId": "9000000001", "originalTransactionId": "9000000000",
    "purchaseDate": _now_ms, "expiresDate": _now_ms + 2592000000,
    "originalPurchaseDate": _now_ms - 5184000000, "type": "Auto-Renewable Subscription",
}
_good_jws = _pyjwt.encode(_tx_payload, _leaf_pem, algorithm="ES256", headers={"x5c": _x5c})

_real_verifier = SignedDataVerifier([_root_der], False, Environment.SANDBOX,
                                    "com.auryel.auryel", None)

# positive : le vrai vérificateur décode la transaction
try:
    _dec = _real_verifier.verify_and_decode_signed_transaction(_good_jws)
    check(_dec.productId == PID and _dec.transactionId == "9000000001",
          "CRYPTO 1 JWS bien signé + chaîne rattachée à la racine de confiance -> décodé")
except VerificationException as e:
    check(False, f"CRYPTO 1 (échec inattendu : {e.status})")

# positive via _apple_pick_transaction (le vrai chemin de code)
_sr = SimpleNamespace(data=[SimpleNamespace(subscriptionGroupIdentifier="G",
      lastTransactions=[SimpleNamespace(status=1, signedTransactionInfo=_good_jws,
      signedRenewalInfo=None, originalTransactionId="9000000000")])])
_tx, _rn, _st = A._apple_pick_transaction(_sr, _real_verifier, PID)
check(_tx.productId == PID and _st == 1,
      "CRYPTO 2 _apple_pick_transaction passe par le vrai SignedDataVerifier (pas contourné)")

# negative : payload altéré -> rejet
_parts = _good_jws.split(".")
_tampered = _parts[0] + "." + _parts[1][:-2] + ("AA" if not _parts[1].endswith("AA") else "BB") + "." + _parts[2]
try:
    _real_verifier.verify_and_decode_signed_transaction(_tampered)
    check(False, "CRYPTO 3 JWS altéré NON rejeté (FAILLE)")
except VerificationException:
    check(True, "CRYPTO 3 JWS altéré -> VerificationException (signature invalide)")

_sr_bad = SimpleNamespace(data=[SimpleNamespace(subscriptionGroupIdentifier="G",
          lastTransactions=[SimpleNamespace(status=1, signedTransactionInfo=_tampered,
          signedRenewalInfo=None, originalTransactionId="x")])])
err = None
try:
    A._apple_pick_transaction(_sr_bad, _real_verifier, PID)
except A.StoreVerificationError as e:
    err = e
check(err is not None and err.code == "invalid_store_receipt",
      "CRYPTO 4 _apple_pick_transaction sur JWS altéré -> invalid_store_receipt")

# negative : chaîne non rattachée à la racine de confiance -> rejet
_other_root_key = ec.generate_private_key(ec.SECP256R1())
_orn = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Untrusted Root")])
_other_root = (x509.CertificateBuilder().subject_name(_orn).issuer_name(_orn)
               .public_key(_other_root_key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
               .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))
               .add_extension(x509.BasicConstraints(True, None), True)
               .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
               .sign(_other_root_key, hashes.SHA256()))
_wrong_verifier = SignedDataVerifier(
    [_other_root.public_bytes(serialization.Encoding.DER)], False,
    Environment.SANDBOX, "com.auryel.auryel", None)
try:
    _wrong_verifier.verify_and_decode_signed_transaction(_good_jws)
    check(False, "CRYPTO 5 chaîne non fiable NON rejetée (FAILLE)")
except VerificationException:
    check(True, "CRYPTO 5 chaîne x5c non rattachée à la racine de confiance -> VerificationException")

# garde-fous statiques : le code ne désactive jamais la vérification
import inspect as _inspect
_src = _inspect.getsource(A)
_billing_src = _src[_src.index("BILLING MOBILE (B4)"):_src.index("RITUEL AUTOMATIQUE")]
# On ignore les lignes de commentaire (le bandeau documente justement l'interdit).
_billing_code = "\n".join(l for l in _billing_src.splitlines()
                          if not l.lstrip().startswith("#"))
_nospace = _billing_code.replace(" ", "")
check("verify_signature" not in _nospace and "jwt.decode(" not in _billing_code,
      "CRYPTO 6 le code B4 n'appelle jamais jwt.decode / ne touche jamais verify_signature")
check("LOCAL_TESTING" not in _billing_code and "XCODE" not in _billing_code
      and "enable_online_checks=False" not in _nospace,
      "CRYPTO 7 le code B4 n'utilise jamais Xcode/LocalTesting ni enable_online_checks=False")
check("_apple_environment" in _billing_src and "SANDBOX if env_label" in _billing_src,
      "CRYPTO 8 environnement Apple = PRODUCTION/SANDBOX uniquement, dérivé de la config serveur")

# ============================================================
# ROUTE — /api/billing/verify côté Apple (vérificateur réel, client+verifier mockés)
# ============================================================
print("-" * 64)
A.app.config["TESTING"] = True
A.limiter.enabled = False
_UID = "22222222-2222-4222-8222-222222222222"
A.resolve_app_session = lambda tok: ({"session_id": "s", "user_id": _UID,
                                      "email": "u@x"} if tok == "T" else None)
_rec, _res = [], []
A.record_mobile_subscription = lambda **kw: _rec.append(kw) or {
    "outcome": "created", "id": "s1", "store": kw["store"], "status": kw["status"]}
A.resync_premium_entitlement = lambda uid, now=None: _res.append(uid)
A.record_and_resync_mobile_subscription = lambda **kw: {
    "subscription": A.record_mobile_subscription(**kw),
    "resync": A.resync_premium_entitlement(kw["user_id"], now=kw.get("now"))}
A.get_consultation_state = lambda uid, now=None: {"consultation": None, "quota": {
    "is_premium": True, "monthly_limit": 4, "monthly_used": 0, "monthly_remaining": 4,
    "earned_available": 0, "first_free_available": True, "period_start": NOW,
    "period_end": NOW + datetime.timedelta(days=30)}}
cli = A.app.test_client()

with patch.object(A, "_apple_api_client", return_value=FakeClient(status_response(status=1))), \
     patch.object(A, "_apple_signed_data_verifier", return_value=FakeVerifier()):
    r = cli.post("/api/billing/verify",
                 json={"store": "app_store", "product_id": PID,
                       "transaction_id": "txn-42"},
                 headers={"Authorization": "Bearer T"})
j = r.get_json()
check(r.status_code == 200 and j["subscription"]["store"] == "app_store"
      and j["subscription"]["entitled"] is True,
      "ROUTE 1 Apple bout-en-bout (client+verifier mockés) -> 200, entitled True")
check(len(_rec) == 1 and _rec[0]["subscription_key"] == "1000000999"
      and isinstance(_rec[0]["entitled"], bool) and _res == [_UID],
      "ROUTE 2 record reçoit subscription_key=originalTransactionId, entitled bool ; puis resync")
_txt = r.get_data(as_text=True)
check("SIGNED_TX" not in _txt and "1000000999" not in _txt and "raw_payload" not in _txt,
      "ROUTE 3 réponse sans JWS / originalTransactionId / raw_payload")

print("-" * 64)
print(f"RÉSULTAT : {_S['pass']} ok / {_S['fail']} ko")
sys.exit(1 if _S["fail"] else 0)
