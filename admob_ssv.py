"""Verification officielle des callbacks AdMob Rewarded SSV.

Ce module ne crédite rien : il vérifie uniquement la signature ECDSA et
fournit les valeurs décodées à la couche métier. Le corps signé est conservé
octet par octet, conformément à la spécification AdMob.
"""

import base64
import json
import threading
import time
from urllib.parse import parse_qs

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

ADMOB_PUBLIC_KEYS_URL = "https://www.gstatic.com/admob/reward/verifier-keys.json"
MAX_KEY_CACHE_SECONDS = 24 * 60 * 60

_keys_lock = threading.Lock()
_keys_cache = {}
_keys_cached_at = 0.0


class SsvError(ValueError):
    pass


def _decode_b64url(value):
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise SsvError("invalid_signature_encoding") from exc


def _fetch_keys(fetcher=requests.get, now=None):
    global _keys_cache, _keys_cached_at
    now = time.time() if now is None else now
    with _keys_lock:
        if _keys_cache and now - _keys_cached_at < MAX_KEY_CACHE_SECONDS:
            return dict(_keys_cache)
        response = fetcher(ADMOB_PUBLIC_KEYS_URL, timeout=5)
        response.raise_for_status()
        payload = response.json()
        parsed = {}
        for item in payload.get("keys", []):
            key_id = str(item.get("keyId"))
            encoded = item.get("base64")
            if not key_id.isdigit() or not encoded:
                continue
            try:
                key = serialization.load_der_public_key(base64.b64decode(encoded))
            except Exception:
                continue
            if isinstance(key, ec.EllipticCurvePublicKey):
                parsed[key_id] = key
        if not parsed:
            raise SsvError("no_trusted_keys")
        _keys_cache = parsed
        _keys_cached_at = now
        return dict(parsed)


def reset_key_cache_for_tests():
    global _keys_cache, _keys_cached_at
    with _keys_lock:
        _keys_cache = {}
        _keys_cached_at = 0.0


def _signed_payload(raw_query):
    """Return raw bytes before the final `&signature=...&key_id=...` pair."""
    marker = b"&signature="
    key_marker = b"&key_id="
    sig_at = raw_query.rfind(marker)
    if sig_at < 0:
        raise SsvError("signature_not_last")
    key_at = raw_query.find(key_marker, sig_at + len(marker))
    if key_at < 0 or raw_query.find(b"&", key_at + len(key_marker)) >= 0:
        raise SsvError("key_id_not_last")
    if not raw_query.startswith(b"signature=", sig_at + 1):
        raise SsvError("invalid_signature_parameter")
    if not raw_query.startswith(b"key_id=", key_at + 1):
        raise SsvError("invalid_key_parameter")
    signature_text = raw_query[sig_at + len(marker):key_at]
    key_id = raw_query[key_at + len(key_marker):]
    if not signature_text or not key_id.isdigit():
        raise SsvError("missing_signature_or_key")
    return raw_query[:sig_at], signature_text, key_id.decode("ascii")


def verify_callback(raw_query, fetcher=requests.get, now=None):
    """Verify one AdMob SSV query and return decoded fields.

    `raw_query` must be the exact bytes from Flask's `request.query_string`.
    """
    if not isinstance(raw_query, bytes):
        raw_query = raw_query.encode("ascii")
    signed, signature_text, key_id = _signed_payload(raw_query)
    keys = _fetch_keys(fetcher=fetcher, now=now)
    key = keys.get(key_id)
    if key is None:
        # One immediate refresh handles a legitimate Google key rotation.
        reset_key_cache_for_tests()
        key = _fetch_keys(fetcher=fetcher, now=now)
        key = key.get(key_id)
    if key is None:
        raise SsvError("unknown_key_id")
    signature = _decode_b64url(signature_text.decode("ascii"))
    try:
        key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
    except Exception as exc:
        raise SsvError("invalid_signature") from exc
    parsed = parse_qs(raw_query.decode("utf-8"), keep_blank_values=True)
    if any(len(items) != 1 for items in parsed.values()):
        raise SsvError("duplicate_parameter")
    values = {k: items[0] for k, items in parsed.items()}
    values.pop("signature", None)
    values.pop("key_id", None)
    values["key_id"] = key_id
    values["signed_query"] = signed
    return values
