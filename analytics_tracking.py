"""First-party analytics primitives and mobile event ingestion.

The allowlist and bounded property sanitizer are deliberately conservative:
analytics can observe product state but cannot become a second content store.
"""

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from functools import wraps

from flask import jsonify, request, g


EVENTS = frozenset({
    "app_opened", "session_started", "account_created", "onboarding_completed",
    "free_consultation_started", "free_consultation_used", "paywall_viewed",
    "premium_purchased", "consultation_started", "consultation_ended",
    "consultation_interrupted", "content_opened", "content_started",
    "content_completed", "wake_scheduled", "wake_triggered", "wake_snoozed",
    "wake_stopped", "notification_opened", "premium_renewal", "premium_expired",
    "premium_refunded", "premium_revoked", "extra_hour_purchase",
    "extra_hour_credited", "extra_hour_refunded",
})
_FORBIDDEN_KEYS = frozenset({
    "content", "message", "prompt", "response", "token", "password", "secret",
    "fcm_token", "apns_token", "api_key", "url", "ebook_text",
})
_ALLOWED_CONTENT_TYPES = frozenset({
    "meditation", "meditation_video", "relaxation_video", "ebook", "exercise",
    "wellbeing_program",
})


def _now():
    return datetime.now(timezone.utc)


def _hash(value, secret):
    return hmac.new((secret or "missing").encode(), str(value).encode(), hashlib.sha256).hexdigest()


def _clean_properties(properties):
    if properties is None:
        return {}
    if not isinstance(properties, dict) or len(properties) > 20:
        raise ValueError("properties_invalid")
    cleaned = {}
    for key, value in properties.items():
        key = str(key).strip()[:48]
        # `content_type` and `content_id` identify a catalog item; they do not
        # carry its body. They are the only content-prefixed keys allowed.
        content_identifier = key.lower() in {"content_type", "content_id"}
        if not key or key.lower() in _FORBIDDEN_KEYS or (not content_identifier and any(part in key.lower() for part in _FORBIDDEN_KEYS)):
            raise ValueError("properties_forbidden")
        if isinstance(value, bool):
            cleaned[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if abs(value) > 10_000_000:
                raise ValueError("property_out_of_range")
            cleaned[key] = value
        elif isinstance(value, str):
            if len(value) > 160:
                raise ValueError("property_too_large")
            cleaned[key] = value
        else:
            raise ValueError("property_type_invalid")
    if len(json.dumps(cleaned, ensure_ascii=False)) > 2048:
        raise ValueError("properties_too_large")
    content_type = cleaned.get("content_type")
    if content_type is not None and content_type not in _ALLOWED_CONTENT_TYPES:
        raise ValueError("content_type_invalid")
    return cleaned


def record_analytics_event(get_conn, secret_key, *, event_name, account_id=None,
                           platform=None, session_id=None, source="server",
                           properties=None, occurred_at=None, idempotency_key=None):
    """Fail-safe append-only writer. Returns False on duplicate or failure."""
    if event_name not in EVENTS:
        return False
    try:
        clean = _clean_properties(properties)
        occurred = occurred_at or _now()
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO analytics_events "
                "(id,event_name,account_id,platform,session_id_hash,source,properties,occurred_at,idempotency_key) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) ON CONFLICT DO NOTHING",
                (str(uuid.uuid4()), event_name, account_id, platform,
                 _hash(session_id, secret_key) if session_id else None,
                 str(source)[:24], json.dumps(clean, ensure_ascii=False), occurred,
                 str(idempotency_key)[:160] if idempotency_key else None),
            )
            inserted = cur.rowcount == 1
            conn.commit()
            return inserted
        finally:
            conn.close()
    except Exception:
        return False


def record_billing_event(get_conn, secret_key, *, account_id, event_type, store,
                         product_id, transaction_id=None, currency=None,
                         gross_amount=None, occurred_at=None, idempotency_key=None):
    """Append a verified store event without ever granting entitlement."""
    if event_type not in {"premium_purchased", "premium_renewal", "premium_expired",
                          "premium_refunded", "premium_revoked", "extra_hour_purchase",
                          "extra_hour_credited", "extra_hour_refunded"}:
        return False
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO billing_events "
                "(id,account_id,event_type,store,product_id,currency,gross_amount,transaction_hash,source,occurred_at,idempotency_key) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'verified_server',%s,%s) ON CONFLICT DO NOTHING",
                (str(uuid.uuid4()), account_id, event_type, store, product_id,
                 currency, gross_amount,
                 _hash(transaction_id, secret_key) if transaction_id else None,
                 occurred_at or _now(), idempotency_key),
            )
            inserted = cur.rowcount == 1
            conn.commit()
            return inserted
        finally:
            conn.close()
    except Exception:
        return False


def record_ai_usage(get_conn, *, request_id, account_id=None, consultation_id=None,
                    advisor_id=None, provider=None, model=None, usage=None,
                    latency_ms=None, status="success", fallback_used=False,
                    occurred_at=None):
    """Store token/latency metadata only; cost stays NULL when unconfigured."""
    usage = usage if isinstance(usage, dict) else {}
    def non_negative(name):
        value = usage.get(name)
        return value if isinstance(value, int) and value >= 0 else None
    input_tokens = non_negative("input_tokens") or non_negative("prompt_tokens")
    output_tokens = non_negative("output_tokens") or non_negative("completion_tokens")
    total_tokens = non_negative("total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """SELECT input_cost_per_token, output_cost_per_token, currency
                   FROM ai_pricing_config
                   WHERE provider=%s AND model=%s AND effective_from<=%s
                   ORDER BY effective_from DESC LIMIT 1""",
                (provider, model, occurred_at or _now()),
            )
            pricing = cur.fetchone()
            estimated_cost = None
            cost_currency = None
            cost_status = "unconfigured"
            if pricing and input_tokens is not None and output_tokens is not None:
                estimated_cost = input_tokens * pricing[0] + output_tokens * pricing[1]
                cost_currency = pricing[2]
                cost_status = "calculated"
            cur.execute(
                """INSERT INTO ai_usage
                   (id,request_id,account_id,consultation_id,advisor_id,provider,model,
                    input_tokens,output_tokens,total_tokens,latency_ms,status,fallback_used,
                    estimated_cost,currency,cost_status,occurred_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (request_id) DO NOTHING""",
                (str(uuid.uuid4()), request_id, account_id, consultation_id, advisor_id,
                 provider, model, input_tokens, output_tokens, total_tokens, latency_ms,
                 status, bool(fallback_used), estimated_cost, cost_currency, cost_status,
                 occurred_at or _now()),
            )
            inserted = cur.rowcount == 1
            conn.commit()
            return inserted
        finally:
            conn.close()
    except Exception:
        return False
def register_analytics_routes(app, get_conn, require_app_auth, secret_key, limiter=None):
    @app.post("/api/app/analytics/events")
    def receive_event():
        # The decorator is applied explicitly to keep this module independent
        # from the monolithic Flask module's import order.
        return _receive()

    receive_event = require_app_auth(receive_event)
    if limiter is not None:
        receive_event = limiter.limit("120 per minute")(receive_event)
    app.view_functions["receive_event"] = receive_event

    def _receive():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "invalid_request"}), 400
        name = data.get("event_name")
        if not isinstance(name, str) or name not in EVENTS:
            return jsonify({"error": "invalid_event"}), 400
        try:
            props = _clean_properties(data.get("properties"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        platform = data.get("platform")
        if platform is not None and platform not in ("android", "ios", "web"):
            return jsonify({"error": "invalid_platform"}), 400
        occurred = _now()
        inserted = record_analytics_event(
            get_conn, secret_key, event_name=name,
            account_id=g.app_account["user_id"], platform=platform,
            session_id=g.app_account.get("session_id"), source="mobile",
            properties=props, occurred_at=occurred,
            idempotency_key=data.get("idempotency_key"),
        )
        return jsonify({"accepted": True, "duplicate": not inserted}), 202

    # Rebind after the decorator so Flask keeps the original endpoint name.
    app.view_functions["receive_event"] = receive_event
