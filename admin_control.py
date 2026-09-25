"""Private AURYEL CONTROL API.

This module deliberately uses opaque bearer sessions instead of Flask cookies.
That keeps the separate dashboard/API boundary free of browser cookie CSRF. A
future cookie mode must add CSRF protection before being enabled.
"""

import hashlib
import hmac
import base64
import json
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo

from flask import g, jsonify, request
from werkzeug.security import check_password_hash
from cryptography.fernet import Fernet, InvalidToken


ADMIN_SESSION_TTL = timedelta(hours=4)
MFA_BOOTSTRAP_TTL = timedelta(minutes=10)
MFA_PENDING_TTL = timedelta(minutes=10)
MFA_STEP_SECONDS = 30
MFA_WINDOW_STEPS = 1
PREMIUM_PRICE_EUR = 4.99
EXTRA_HOUR_PRICE_EUR = 1.99


def _now():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _json_row(value):
    return _iso(value)


def _hash(value, secret):
    return hmac.new((secret or "missing-secret").encode(), value.encode(), hashlib.sha256).hexdigest()


def _email(value):
    return str(value or "").strip().lower()


def _mask_email(value):
    if not value or "@" not in value:
        return None
    local, domain = value.split("@", 1)
    return f"{(local[:1] or '*')}***@{domain}"


def _mfa_fernet(secret_key):
    key = hashlib.sha256((secret_key or "missing-secret").encode() + b"|auryel-control-mfa").digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _new_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _totp_value(secret, timestep):
    padded = secret + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", timestep), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % 1_000_000:06d}"


def _verify_totp(secret, code, now=None, last_step=None):
    if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
        return None
    current = int((now or time.time()) // MFA_STEP_SECONDS)
    for step in range(current - MFA_WINDOW_STEPS, current + MFA_WINDOW_STEPS + 1):
        if last_step is not None and step <= int(last_step):
            continue
        if hmac.compare_digest(_totp_value(secret, step), code):
            return step
    return None


def _recovery_hash(code, secret_key):
    normalized = str(code or "").strip().replace("-", "").upper()
    return hmac.new((secret_key or "missing-secret").encode(),
                    ("auryel-recovery:" + normalized).encode(), hashlib.sha256).hexdigest()


def _recovery_codes(secret_key, count=10):
    codes = [secrets.token_hex(5).upper() for _ in range(count)]
    return codes, [_recovery_hash(code, secret_key) for code in codes]


def _json_list(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = []
    return value if isinstance(value, list) else []


def _provisioning_uri(secret, email):
    from urllib.parse import quote
    label = "Auryel Control:" + str(email or "admin")
    return ("otpauth://totp/" + quote(label) + "?secret=" + secret +
            "&issuer=" + quote("Auryel Control") +
            "&algorithm=SHA1&digits=6&period=30")


def _window(name):
    name = name or "30d"
    if name not in {"today", "7d", "30d"}:
        name = "30d"
    now = _now()
    if name == "today":
        paris = datetime.now(ZoneInfo("Europe/Paris"))
        start = paris.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    else:
        start = now - timedelta(days=7 if name == "7d" else 30)
    return name, start, now


def _audit(conn, admin_id, action, result, target_type=None, target_id=None, metadata=None):
    # Metadata is intentionally small and caller-controlled; never put tokens,
    # passwords, message text or credentials here.
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO admin_audit_events "
        "(admin_id,action,target_type,target_id,result,metadata) VALUES (%s,%s,%s,%s,%s,%s)",
        (admin_id, action[:80], (target_type or "")[:80] or None,
         (str(target_id)[:120] if target_id is not None else None), result[:40], metadata),
    )


def _error(message, status):
    return jsonify({"error": message}), status


def register_admin_control(app, get_conn, limiter, secret_key, guides=None):
    """Register the isolated admin API on the existing Flask app."""

    @app.after_request
    def _admin_no_store(response):
        if request.path.startswith("/api/admin/"):
            response.headers["Cache-Control"] = "no-store"
            if request.is_secure:
                response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    def _session_from_request():
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        token = header[7:].strip()
        if len(token) < 32:
            return None
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT s.id,s.admin_id,s.expires_at,a.email,a.role,a.active,a.mfa_enabled,"
                "COALESCE(s.scope,'full') "
                "FROM admin_sessions s JOIN admin_accounts a ON a.id=s.admin_id "
                "WHERE s.token_hash=%s AND s.revoked_at IS NULL AND s.expires_at>%s",
                (_hash(token, secret_key), _now()),
            )
            row = cur.fetchone()
            if not row or not row[5] or row[4] not in ("admin", "owner"):
                conn.close()
                return None
            cur.execute("UPDATE admin_sessions SET last_used_at=%s WHERE id=%s", (_now(), row[0]))
            conn.commit()
            return {"session_id": row[0], "admin_id": row[1], "email": row[3],
                    "role": row[4], "mfa_enabled": bool(row[6]), "scope": row[7]}
        except Exception:
            conn.rollback()
            raise
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def admin_required(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            auth = _session_from_request()
            if not auth or auth["scope"] != "full":
                return _error("admin_auth_required", 401)
            g.admin_auth = auth
            return fn(*args, **kwargs)
        return wrapped

    def admin_bootstrap_or_full_required(fn):
        """Allow only an opaque MFA bootstrap session or a full admin session."""
        @wraps(fn)
        def wrapped(*args, **kwargs):
            auth = _session_from_request()
            if not auth or auth["scope"] not in ("mfa_bootstrap", "full"):
                return _error("admin_auth_required", 401)
            g.admin_auth = auth
            return fn(*args, **kwargs)
        return wrapped

    def _with_audit(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            response = fn(*args, **kwargs)
            auth = getattr(g, "admin_auth", None)
            if auth:
                conn = get_conn()
                try:
                    _audit(conn, auth["admin_id"], request.endpoint or "admin_action", "success")
                    conn.commit()
                finally:
                    conn.close()
            return response
        return wrapped

    @app.post("/api/admin/auth/login", endpoint="admin_control_login")
    @limiter.limit("5 per 15 minutes")
    def admin_login():
        body = request.get_json(silent=True) or {}
        email = _email(body.get("email"))
        password = body.get("password")
        generic = _error("invalid_credentials", 401)
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id,email,password_hash,role,active,mfa_enabled,mfa_secret_encrypted,mfa_last_step,mfa_recovery_hashes FROM admin_accounts WHERE email_normalized=%s FOR UPDATE", (email,))
            row = cur.fetchone()
            valid = bool(row and row[4] and row[3] in ("admin", "owner") and isinstance(password, str)
                        and check_password_hash(row[2], password))
            if not valid:
                _audit(conn, row[0] if row else None, "admin_login", "failure")
                conn.commit()
                return generic
            if row[5]:
                mfa_code = body.get("mfa_code")
                if not mfa_code:
                    _audit(conn, row[0], "admin_login", "mfa_required")
                    conn.commit()
                    return _error("mfa_required", 401)
                try:
                    secret = _mfa_fernet(secret_key).decrypt(row[6].encode()).decode() if row[6] else None
                except (InvalidToken, ValueError, TypeError):
                    secret = None
                step = _verify_totp(secret, mfa_code, last_step=row[7]) if secret else None
                recovery = _json_list(row[8])
                recovery_hash = _recovery_hash(mfa_code, secret_key) if mfa_code else None
                if step is None and recovery_hash in recovery:
                    recovery.remove(recovery_hash)
                    cur.execute("UPDATE admin_accounts SET mfa_recovery_hashes=%s,updated_at=%s WHERE id=%s", (json.dumps(recovery), _now(), row[0]))
                elif step is not None:
                    cur.execute("UPDATE admin_accounts SET mfa_last_step=%s,updated_at=%s WHERE id=%s", (step, _now(), row[0]))
                else:
                    _audit(conn, row[0], "admin_login", "mfa_failure")
                    conn.commit()
                    return _error("invalid_mfa", 401)
            raw_token = secrets.token_urlsafe(48)
            now = _now()
            session_scope = "full" if row[5] else "mfa_bootstrap"
            session_ttl = ADMIN_SESSION_TTL if session_scope == "full" else MFA_BOOTSTRAP_TTL
            cur.execute(
                "INSERT INTO admin_sessions (id,admin_id,token_hash,expires_at,created_at,last_used_at,ip_hash,user_agent_hash,scope) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (secrets.token_hex(16), row[0], _hash(raw_token, secret_key), now + session_ttl,
                 now, now, _hash(request.remote_addr or "", secret_key),
                 _hash(request.headers.get("User-Agent", "")[:256], secret_key), session_scope),
            )
            cur.execute("UPDATE admin_accounts SET last_login_at=%s,updated_at=%s WHERE id=%s", (now, now, row[0]))
            _audit(conn, row[0], "admin_login", "mfa_bootstrap" if session_scope == "mfa_bootstrap" else "success")
            conn.commit()
            return jsonify({"session_token": raw_token, "expires_at": _iso(now + session_ttl),
                            "admin": {"id": str(row[0]), "email": row[1], "role": row[3],
                                       "mfa_required": bool(row[5]),
                                       "mfa_enrollment_required": not bool(row[5])},
                            "session_scope": session_scope}), 200
        finally:
            conn.close()

    @app.post("/api/admin/auth/logout", endpoint="admin_control_logout")
    @admin_bootstrap_or_full_required
    def admin_logout():
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE admin_sessions SET revoked_at=%s WHERE id=%s", (_now(), g.admin_auth["session_id"]))
            _audit(conn, g.admin_auth["admin_id"], "admin_logout", "success")
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.get("/api/admin/auth/me", endpoint="admin_control_me")
    @admin_bootstrap_or_full_required
    def admin_me():
        return jsonify({"admin": {"id": str(g.admin_auth["admin_id"]), "email": g.admin_auth["email"],
                                   "role": g.admin_auth["role"], "mfa_enabled": g.admin_auth["mfa_enabled"]},
                        "session": {"auth_mode": "bearer", "scope": g.admin_auth["scope"],
                                    "mfa_verified": g.admin_auth["scope"] == "full",
                                    "csrf": "not_applicable"}})

    def _mfa_admin_row(conn, admin_id):
        cur = conn.cursor()
        cur.execute("SELECT email,mfa_enabled,mfa_secret_encrypted,mfa_pending_expires_at,mfa_last_step,mfa_recovery_hashes FROM admin_accounts WHERE id=%s FOR UPDATE", (admin_id,))
        return cur.fetchone()

    def _verify_admin_factor(conn, admin_id, code):
        row = _mfa_admin_row(conn, admin_id)
        if not row or not row[1]:
            return False
        try:
            secret = _mfa_fernet(secret_key).decrypt(row[2].encode()).decode() if row[2] else None
        except (InvalidToken, ValueError, TypeError):
            secret = None
        step = _verify_totp(secret, code, last_step=row[4]) if secret else None
        recovery = _json_list(row[5])
        recovery_hash = _recovery_hash(code, secret_key)
        if step is None and recovery_hash in recovery:
            recovery.remove(recovery_hash)
            conn.cursor().execute("UPDATE admin_accounts SET mfa_recovery_hashes=%s,updated_at=%s WHERE id=%s", (json.dumps(recovery), _now(), admin_id))
            return True
        if step is None:
            return False
        conn.cursor().execute("UPDATE admin_accounts SET mfa_last_step=%s,updated_at=%s WHERE id=%s", (step, _now(), admin_id))
        return True

    @app.post("/api/admin/auth/mfa/enroll", endpoint="admin_control_mfa_enroll")
    @admin_bootstrap_or_full_required
    def admin_mfa_enroll():
        conn = get_conn()
        try:
            row = _mfa_admin_row(conn, g.admin_auth["admin_id"])
            if not row or row[1]:
                return _error("mfa_already_enabled", 409)
            secret = _new_totp_secret()
            expires = _now() + MFA_PENDING_TTL
            encrypted = _mfa_fernet(secret_key).encrypt(secret.encode()).decode()
            cur = conn.cursor()
            cur.execute("UPDATE admin_accounts SET mfa_method='totp',mfa_secret_encrypted=%s,mfa_pending_expires_at=%s,updated_at=%s WHERE id=%s", (encrypted, expires, _now(), g.admin_auth["admin_id"]))
            _audit(conn, g.admin_auth["admin_id"], "mfa_enrollment_started", "success")
            conn.commit()
            return jsonify({"provisioning_uri": _provisioning_uri(secret, row[0]), "secret": secret, "expires_at": _iso(expires)})
        finally:
            conn.close()

    @app.post("/api/admin/auth/mfa/confirm", endpoint="admin_control_mfa_confirm")
    @admin_bootstrap_or_full_required
    def admin_mfa_confirm():
        conn = get_conn()
        try:
            row = _mfa_admin_row(conn, g.admin_auth["admin_id"])
            if not row or row[1] or not row[2] or not row[3] or row[3] <= _now():
                return _error("mfa_enrollment_expired", 400)
            try:
                secret = _mfa_fernet(secret_key).decrypt(row[2].encode()).decode()
            except (InvalidToken, ValueError, TypeError):
                return _error("mfa_enrollment_invalid", 400)
            body = request.get_json(silent=True) or {}
            step = _verify_totp(secret, body.get("code"))
            if step is None:
                _audit(conn, g.admin_auth["admin_id"], "mfa_enrollment_confirm", "failure")
                conn.commit()
                return _error("invalid_mfa", 401)
            codes, hashes = _recovery_codes(secret_key)
            cur = conn.cursor()
            cur.execute("UPDATE admin_accounts SET mfa_enabled=TRUE,mfa_method='totp',mfa_enrolled_at=%s,mfa_pending_expires_at=NULL,mfa_recovery_hashes=%s,mfa_recovery_generated_at=%s,mfa_last_step=%s,updated_at=%s WHERE id=%s", (_now(), json.dumps(hashes), _now(), step, _now(), g.admin_auth["admin_id"]))
            if g.admin_auth["scope"] == "mfa_bootstrap":
                cur.execute("UPDATE admin_sessions SET scope='full',expires_at=%s,last_used_at=%s WHERE id=%s AND revoked_at IS NULL", (_now() + ADMIN_SESSION_TTL, _now(), g.admin_auth["session_id"]))
            _audit(conn, g.admin_auth["admin_id"], "mfa_enrollment_confirm", "success")
            conn.commit()
            return jsonify({"mfa_enabled": True, "recovery_codes": codes})
        finally:
            conn.close()

    @app.post("/api/admin/auth/mfa/recovery-codes", endpoint="admin_control_mfa_recovery")
    @admin_required
    def admin_mfa_recovery():
        conn = get_conn()
        try:
            body = request.get_json(silent=True) or {}
            if not _verify_admin_factor(conn, g.admin_auth["admin_id"], body.get("code")):
                conn.rollback()
                return _error("invalid_mfa", 401)
            codes, hashes = _recovery_codes(secret_key)
            cur = conn.cursor()
            cur.execute("UPDATE admin_accounts SET mfa_recovery_hashes=%s,mfa_recovery_generated_at=%s,updated_at=%s WHERE id=%s", (json.dumps(hashes), _now(), _now(), g.admin_auth["admin_id"]))
            _audit(conn, g.admin_auth["admin_id"], "mfa_recovery_regenerated", "success")
            conn.commit()
            return jsonify({"recovery_codes": codes})
        finally:
            conn.close()

    @app.post("/api/admin/auth/mfa/disable", endpoint="admin_control_mfa_disable")
    @admin_required
    def admin_mfa_disable():
        conn = get_conn()
        try:
            body = request.get_json(silent=True) or {}
            if not _verify_admin_factor(conn, g.admin_auth["admin_id"], body.get("code")):
                conn.rollback()
                return _error("invalid_mfa", 401)
            cur = conn.cursor()
            cur.execute("UPDATE admin_accounts SET mfa_enabled=FALSE,mfa_method=NULL,mfa_secret_encrypted=NULL,mfa_pending_expires_at=NULL,mfa_recovery_hashes='[]'::jsonb,mfa_last_step=NULL,updated_at=%s WHERE id=%s", (_now(), g.admin_auth["admin_id"]))
            _audit(conn, g.admin_auth["admin_id"], "mfa_disabled", "success")
            conn.commit()
            return jsonify({"mfa_enabled": False})
        finally:
            conn.close()

    def _query_value(sql, params=(), default=0):
        conn = get_conn()
        try:
            cur = conn.cursor(); cur.execute(sql, params); row = cur.fetchone()
            return row[0] if row and row[0] is not None else default
        finally:
            conn.close()

    def _aggregate_window(window):
        name, start, end = _window(window)
        params = (start, end)
        total_users = _query_value("SELECT COUNT(*) FROM accounts WHERE deleted_at IS NULL")
        new_users = _query_value("SELECT COUNT(*) FROM accounts WHERE deleted_at IS NULL AND created_at >= %s AND created_at < %s", params)
        active = _query_value("SELECT COUNT(DISTINCT user_id) FROM mobile_subscriptions WHERE store IN ('google_play','app_store') AND entitled=TRUE AND expires_at>%s", (end,))
        expired = _query_value("SELECT COUNT(DISTINCT user_id) FROM mobile_subscriptions WHERE expires_at IS NOT NULL AND expires_at<=%s", (end,))
        consultations = _query_value("SELECT COUNT(*) FROM consultations WHERE created_at >= %s AND created_at < %s", params)
        consumed = _query_value("SELECT COALESCE(SUM(-delta_seconds),0) FROM time_ledger WHERE delta_seconds<0 AND created_at >= %s AND created_at < %s", params)
        earned_stars = _query_value("SELECT COALESCE(SUM(delta_stars),0) FROM reward_transactions WHERE delta_stars>0 AND created_at >= %s AND created_at < %s", params)
        spent_stars = _query_value("SELECT COALESCE(SUM(-delta_stars),0) FROM reward_transactions WHERE delta_stars<0 AND created_at >= %s AND created_at < %s", params)
        express = _query_value("SELECT COUNT(*) FROM express_consultations WHERE created_at >= %s AND created_at < %s", params)
        sent = _query_value("SELECT COUNT(*) FROM notification_sends WHERE status='sent' AND created_at >= %s AND created_at < %s", params)
        failed = _query_value("SELECT COUNT(*) FROM notification_sends WHERE status='failed' AND created_at >= %s AND created_at < %s", params)
        active_devices = _query_value("SELECT COUNT(*) FROM push_devices WHERE enabled=TRUE AND revoked_at IS NULL AND invalid_at IS NULL")
        dau = _query_value("SELECT COUNT(DISTINCT user_id) FROM app_sessions WHERE last_used_at >= %s AND last_used_at < %s", params)
        return {"window": name, "total_mobile_accounts": int(total_users), "new_accounts": int(new_users),
                "active_premium": int(active), "expired_premium": int(expired), "consultations": int(consultations),
                "consultation_time_seconds": int(consumed), "stars_earned": int(earned_stars),
                "stars_spent": int(spent_stars), "express_unlocks": int(express), "push_sent": int(sent),
                "push_failed": int(failed), "active_push_devices": int(active_devices),
                "dau_proxy": int(dau), "activity_reliability": "proxy_app_sessions_last_used_at"}

    @app.get("/api/admin/overview", endpoint="admin_control_overview")
    @admin_required
    @_with_audit
    def admin_overview():
        return jsonify({"windows": {key: _aggregate_window(key) for key in ("today", "7d", "30d")},
                        "reliability": "DAU/WAU/MAU are session-activity proxies, not event-complete retention"})

    @app.get("/api/admin/users", endpoint="admin_control_users")
    @admin_required
    @_with_audit
    def admin_users():
        try: page = max(1, int(request.args.get("page", 1)))
        except ValueError: page = 1
        try: per_page = min(100, max(1, int(request.args.get("per_page", 25))))
        except ValueError: per_page = 25
        search = request.args.get("search", "").strip().lower()[:120]
        premium = request.args.get("premium")
        platform = request.args.get("platform")
        clauses = ["a.deleted_at IS NULL"]; params = []
        if search:
            clauses.append("(a.email_normalized LIKE %s OR a.user_id::text=%s)"); params += [f"%{search}%", search]
        if premium == "true": clauses.append("EXISTS (SELECT 1 FROM mobile_subscriptions s WHERE s.user_id=a.user_id AND s.entitled=TRUE AND s.expires_at>NOW())")
        if premium == "false": clauses.append("NOT EXISTS (SELECT 1 FROM mobile_subscriptions s WHERE s.user_id=a.user_id AND s.entitled=TRUE AND s.expires_at>NOW())")
        if platform in ("android", "ios"): clauses.append("EXISTS (SELECT 1 FROM app_sessions ps WHERE ps.user_id=a.user_id AND ps.platform=%s)"); params.append(platform)
        where = " AND ".join(clauses)
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM accounts a WHERE {where}", params); total = cur.fetchone()[0]
            params += [per_page, (page - 1) * per_page]
            cur.execute(f"""WITH latest_session AS (
                SELECT DISTINCT ON (user_id) user_id,last_used_at,platform FROM app_sessions ORDER BY user_id,last_used_at DESC NULLS LAST
            ), current_sub AS (
                SELECT DISTINCT ON (user_id) user_id,entitled,expires_at,store FROM mobile_subscriptions ORDER BY user_id,expires_at DESC NULLS LAST
            )
            SELECT a.user_id,a.email,a.created_at,a.last_login_at,p.prenom,p.guide,ls.last_used_at,ls.platform,
                   cs.entitled,cs.expires_at,cs.store,
                   COALESCE(a.first_free_seconds_remaining,0)+COALESCE(a.earned_seconds_remaining,0)+COALESCE(a.purchased_seconds_remaining,0)+
                   COALESCE((SELECT GREATEST(0,monthly_allowance_seconds-monthly_used_seconds) FROM consultation_allowance ca WHERE ca.user_id=a.user_id AND ca.period_start<=NOW() AND ca.period_end>NOW() ORDER BY ca.period_start DESC LIMIT 1),0) AS time_available,
                   COALESCE((SELECT stars_balance FROM reward_wallet rw WHERE rw.user_id=a.user_id),0) AS stars
            FROM accounts a LEFT JOIN app_profiles p ON p.user_id=a.user_id LEFT JOIN latest_session ls ON ls.user_id=a.user_id
            LEFT JOIN current_sub cs ON cs.user_id=a.user_id WHERE {where} ORDER BY a.created_at DESC NULLS LAST LIMIT %s OFFSET %s""", params)
            rows = cur.fetchall()
            items = []
            for r in rows:
                last = max([x for x in (r[3], r[6]) if x is not None], default=None)
                items.append({"account_id": str(r[0]), "email_masked": _mask_email(r[1]), "prenom": r[4], "created_at": _iso(r[2]),
                              "last_activity_at": _iso(last), "platform": r[7], "premium_active": bool(r[8] and r[9] and r[9]>_now()),
                              "premium_expiration": _iso(r[9]), "premium_store": r[10], "time_available_seconds": int(r[11] or 0),
                              "stars": int(r[12] or 0), "preferred_advisor": r[5]})
            return jsonify({"items": items, "page": page, "per_page": per_page, "total": total})
        finally: conn.close()

    @app.get("/api/admin/users/<user_id>", endpoint="admin_control_user_detail")
    @admin_required
    @_with_audit
    def admin_user_detail(user_id):
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""SELECT a.user_id,a.email,a.created_at,a.last_login_at,p.prenom,p.guide,
                COALESCE((SELECT stars_balance FROM reward_wallet WHERE user_id=a.user_id),0),
                COALESCE((SELECT COUNT(*) FROM consultations WHERE user_id=a.user_id),0),
                COALESCE((SELECT SUM(GREATEST(-delta_seconds,0)) FROM time_ledger WHERE user_id=a.user_id),0),
                COALESCE((SELECT COUNT(*) FROM messages WHERE user_id=a.user_id),0)
                FROM accounts a LEFT JOIN app_profiles p ON p.user_id=a.user_id WHERE a.user_id::text=%s AND a.deleted_at IS NULL""", (user_id,))
            r = cur.fetchone()
            if not r: return _error("not_found", 404)
            cur.execute("SELECT store,product_id,status,entitled,purchased_at,expires_at FROM mobile_subscriptions WHERE user_id=%s ORDER BY expires_at DESC NULLS LAST", (r[0],))
            billing = [{"store": x[0], "product_id": x[1], "status": x[2], "entitled": bool(x[3]), "purchased_at": _iso(x[4]), "expires_at": _iso(x[5])} for x in cur.fetchall()]
            return jsonify({"account_id": str(r[0]), "email_masked": _mask_email(r[1]), "created_at": _iso(r[2]), "last_login_at": _iso(r[3]),
                            "prenom": r[4], "preferred_advisor": r[5], "stars": int(r[6] or 0), "consultations": int(r[7] or 0),
                            "time_consumed_seconds": int(r[8] or 0), "message_count": int(r[9] or 0), "billing": billing,
                            "private_message_content": "excluded_by_design"})
        finally: conn.close()

    @app.get("/api/admin/billing/overview", endpoint="admin_control_billing")
    @admin_required
    @_with_audit
    def admin_billing():
        result = {}
        for name, start, end in (_window(x) for x in ("today", "7d", "30d")):
            conn = get_conn()
            try:
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM mobile_subscriptions WHERE entitled=TRUE AND expires_at>%s", (end,)); active = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM mobile_subscriptions WHERE purchased_at >= %s AND purchased_at < %s", (start,end)); premium_new = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM mobile_subscriptions WHERE expires_at IS NOT NULL AND expires_at<=%s", (end,)); expired = c.fetchone()[0]
                c.execute("SELECT store,COUNT(*) FROM mobile_subscriptions WHERE purchased_at >= %s AND purchased_at < %s GROUP BY store", (start,end)); stores = dict(c.fetchall())
                c.execute("SELECT store,COUNT(*) FROM mobile_purchases WHERE purchased_at >= %s AND purchased_at < %s GROUP BY store", (start,end)); extras = dict(c.fetchall())
                extra_count = sum(extras.values())
                result[name] = {"active_premium": active, "new_premium": premium_new, "expired_premium": expired,
                                "premium_google": stores.get("google_play",0), "premium_apple": stores.get("app_store",0),
                                "extra_hour_google": extras.get("google_play",0), "extra_hour_apple": extras.get("app_store",0),
                                "transactions_count": premium_new + extra_count,
                                "estimated_gross_revenue": round(premium_new*PREMIUM_PRICE_EUR + extra_count*EXTRA_HOUR_PRICE_EUR, 2),
                                "financial_accuracy": "estimated_catalog_gross"}
            finally: conn.close()
        return jsonify({"windows": result, "currency": "EUR", "note": "Excludes taxes, store commissions, refunds and net cash collected."})

    @app.get("/api/admin/consultations/overview", endpoint="admin_control_consultations")
    @admin_required
    @_with_audit
    def admin_consultations():
        name, start, end = _window(request.args.get("window"))
        conn = get_conn()
        try:
            c = conn.cursor(); c.execute("SELECT credit_source,COUNT(*) FROM consultations WHERE created_at >= %s AND created_at < %s GROUP BY credit_source", (start,end)); sources = dict(c.fetchall())
            c.execute("SELECT COUNT(*),COALESCE(SUM(GREATEST(-delta_seconds,0)),0) FROM time_ledger WHERE delta_seconds<0 AND created_at >= %s AND created_at < %s", (start,end)); count_time = c.fetchone()
            c.execute("SELECT AVG(EXTRACT(EPOCH FROM (COALESCE(last_activity_at,expires_at)-started_at))) FROM consultations WHERE created_at >= %s AND created_at < %s AND COALESCE(last_activity_at,expires_at)>=started_at", (start,end)); avg = c.fetchone()[0]
            return jsonify({"window": name, "consultations": sum(sources.values()), "total_time_consumed": int(count_time[1] or 0),
                            "average_duration_seconds": float(avg) if avg is not None else None, "average_duration_reliability": "partial_activity_timestamps",
                            "by_credit_source": sources})
        finally: conn.close()

    @app.get("/api/admin/advisors", endpoint="admin_control_advisors")
    @admin_required
    @_with_audit
    def admin_advisors():
        name, start, end = _window(request.args.get("window")); conn = get_conn()
        try:
            c = conn.cursor(); c.execute("""WITH base AS (SELECT advisor_id,COUNT(*) consultations,COUNT(DISTINCT user_id) users FROM consultations WHERE created_at >= %s AND created_at < %s GROUP BY advisor_id),
            repeated AS (SELECT advisor_id,COUNT(*) users_returning FROM (SELECT advisor_id,user_id FROM consultations WHERE created_at >= %s AND created_at < %s GROUP BY advisor_id,user_id HAVING COUNT(*)>1) x GROUP BY advisor_id),
            msgs AS (SELECT c.advisor_id,COUNT(m.id) message_count FROM consultations c LEFT JOIN messages m ON m.consultation_id=c.id WHERE c.created_at >= %s AND c.created_at < %s GROUP BY c.advisor_id),
            times AS (SELECT c.advisor_id,COALESCE(SUM(GREATEST(-t.delta_seconds,0)),0) seconds FROM consultations c JOIN time_ledger t ON t.ref_id::text=c.id::text AND t.reason='consultation_debit' WHERE c.created_at >= %s AND c.created_at < %s AND t.delta_seconds<0 GROUP BY c.advisor_id)
            SELECT b.advisor_id,b.users,b.consultations,COALESCE(m.message_count,0),COALESCE(t.seconds,0),COALESCE(r.users_returning,0) FROM base b LEFT JOIN msgs m USING(advisor_id) LEFT JOIN times t USING(advisor_id) LEFT JOIN repeated r USING(advisor_id) ORDER BY b.consultations DESC""", (start,end,start,end,start,end,start,end))
            items=[]
            for r in c.fetchall():
                info=(guides or {}).get(r[0], {})
                items.append({"advisor_id":r[0],"name":info.get("nom",r[0]),"users":r[1],"consultations":r[2],"message_count":r[3],"time_consumed":int(r[4]),"return_to_same_advisor": {"users":r[5],"reliability":"consultation_history"}})
            return jsonify({"window":name,"items":items})
        finally: conn.close()

    @app.get("/api/admin/stars/overview", endpoint="admin_control_stars")
    @admin_required
    @_with_audit
    def admin_stars():
        name,start,end=_window(request.args.get("window")); conn=get_conn()
        try:
            c=conn.cursor(); c.execute("SELECT COALESCE(SUM(stars_balance),0) FROM reward_wallet"); circulation=c.fetchone()[0]
            def totals(a,b):
                c.execute("SELECT COALESCE(SUM(delta_stars) FILTER(WHERE delta_stars>0),0),COALESCE(SUM(-delta_stars) FILTER(WHERE delta_stars<0),0) FROM reward_transactions WHERE created_at >= %s AND created_at < %s",(a,b)); return c.fetchone()
            earned,spent=totals(start,end); c.execute("SELECT COALESCE(source_type,reason),SUM(delta_stars) FROM reward_transactions WHERE delta_stars>0 AND created_at >= %s AND created_at < %s GROUP BY 1",(start,end)); by_source=dict(c.fetchall()); c.execute("SELECT COALESCE(reason,type),SUM(-delta_stars) FROM reward_transactions WHERE delta_stars<0 AND created_at >= %s AND created_at < %s GROUP BY 1",(start,end)); by_use=dict(c.fetchall()); c.execute("SELECT COUNT(*) FROM express_consultations WHERE created_at >= %s AND created_at < %s",(start,end)); express=c.fetchone()[0]
            return jsonify({"window":name,"stars_in_circulation":int(circulation),"earned":int(earned),"spent":int(spent),"by_source":by_source,"by_use":by_use,"express_unlocks":express,"rewarded_ads":by_source.get("rewarded_ad_completed",0),"mini_games":by_source.get("mini_game_completed",0)})
        finally: conn.close()

    @app.get("/api/admin/content/overview", endpoint="admin_control_content")
    @admin_required
    @_with_audit
    def admin_content():
        conn=get_conn()
        try:
            c=conn.cursor(); tables=("meditation_catalog","meditation_video_catalog","relaxation_video_catalog","wellbeing_ebooks","exercise_catalog","wake_video_catalog")
            catalog={}
            for table in tables:
                c.execute(f"SELECT COUNT(*) FROM {table} WHERE is_active=TRUE" if table != "meditation_catalog" else "SELECT COUNT(*) FROM meditation_catalog")
                catalog[table]=c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM mini_game_sessions"); games=c.fetchone()[0]; c.execute("SELECT COUNT(DISTINCT user_id) FROM wellbeing_program_days WHERE completed_at IS NOT NULL"); days=c.fetchone()[0]; c.execute("SELECT COUNT(*) FROM wellbeing_program_actions WHERE completed_at IS NOT NULL"); actions=c.fetchone()[0]; c.execute("SELECT COUNT(*) FROM content_recommendations WHERE opened_at IS NOT NULL"); opened=c.fetchone()[0]
            return jsonify({"catalog":catalog,"usage":{"mini_game_sessions":games,"wellbeing_program_users":days,"completed_program_days":days,"completed_program_actions":actions,"recommendations_opened":opened},"tracking_note":"First-party lifecycle events cover available content open/start/completion points; direct reading duration and some completion states remain partial."})
        finally: conn.close()

    @app.get("/api/admin/notifications/overview", endpoint="admin_control_notifications")
    @admin_required
    @_with_audit
    def admin_notifications():
        name,start,end=_window(request.args.get("window")); conn=get_conn()
        try:
            c=conn.cursor(); c.execute("SELECT COUNT(*) FILTER(WHERE enabled AND revoked_at IS NULL AND invalid_at IS NULL),COUNT(*) FILTER(WHERE platform='android' AND enabled AND revoked_at IS NULL AND invalid_at IS NULL),COUNT(*) FILTER(WHERE platform='ios' AND enabled AND revoked_at IS NULL AND invalid_at IS NULL),COUNT(*) FILTER(WHERE invalid_at IS NOT NULL) FROM push_devices"); devices=c.fetchone(); c.execute("SELECT status,COUNT(*) FROM notification_sends WHERE created_at >= %s AND created_at < %s GROUP BY status",(start,end)); by_status=dict(c.fetchall()); c.execute("SELECT category,COUNT(*) FROM notification_sends WHERE created_at >= %s AND created_at < %s GROUP BY category",(start,end)); cats=dict(c.fetchall()); sent=by_status.get("sent",0); failed=by_status.get("failed",0)
            return jsonify({"window":name,"active_devices":devices[0],"android_devices":devices[1],"ios_devices":devices[2],"invalid_devices":devices[3],"sent":sent+failed,"success":sent,"failed":failed,"success_rate":round(sent/(sent+failed),4) if sent+failed else None,"by_category":cats})
        finally: conn.close()

    @app.get("/api/admin/technical/overview", endpoint="admin_control_technical")
    @admin_required
    @_with_audit
    def admin_technical():
        conn=get_conn()
        try:
            c=conn.cursor(); c.execute("SELECT 1"); db="ok" if c.fetchone()[0]==1 else "error"; c.execute("SELECT status,last_run_at,errors FROM r2_sync_state WHERE id=1"); r=c.fetchone(); c.execute("SELECT COUNT(*) FROM notification_sends WHERE status='failed' AND created_at>=NOW()-INTERVAL '24 hours'"); push=c.fetchone()[0]; c.execute("SELECT COUNT(*) FROM mobile_subscriptions WHERE status IN ('rejected','invalid') AND updated_at>=NOW()-INTERVAL '24 hours'"); billing=c.fetchone()[0]
            return jsonify({"database_health":db,"r2_status": {"status":r[0],"last_run_at":_iso(r[1]),"errors":int(r[2] or 0)} if r else {"status":"unknown"},"push_failures_24h":push,"billing_failures_24h":billing})
        finally: conn.close()

    @app.get("/api/admin/ai/overview", endpoint="admin_control_ai")
    @admin_required
    @_with_audit
    def admin_ai():
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute("""SELECT COUNT(*),COALESCE(SUM(total_tokens),0),COALESCE(SUM(estimated_cost),0),
                        COALESCE(AVG(latency_ms),0),COUNT(*) FILTER(WHERE status<>'success')
                        FROM ai_usage WHERE occurred_at>=NOW()-INTERVAL '24 hours'""")
            today = c.fetchone()
            c.execute("""SELECT COUNT(*),COALESCE(SUM(total_tokens),0),COALESCE(SUM(estimated_cost),0),
                        COALESCE(AVG(latency_ms),0),COUNT(*) FILTER(WHERE status<>'success')
                        FROM ai_usage WHERE occurred_at>=NOW()-INTERVAL '30 days'""")
            month = c.fetchone()
            c.execute("SELECT COUNT(*) FROM ai_usage WHERE cost_status='calculated' AND occurred_at>=NOW()-INTERVAL '30 days'")
            configured = c.fetchone()[0]
            return jsonify({
                "usage_tracking": True,
                "cost_tracking": configured > 0,
                "requests_today": int(today[0]), "tokens_today": int(today[1]),
                "estimated_cost_today": float(today[2]), "errors_today": int(today[4]),
                "latency_ms_today": float(today[3]), "requests_30d": int(month[0]),
                "tokens_30d": int(month[1]), "estimated_cost_30d": float(month[2]),
                "errors_30d": int(month[4]), "latency_ms_30d": float(month[3]),
                "cost_status": "calculated" if configured > 0 else "unconfigured",
                "note": "Les coûts sont estimés uniquement lorsqu'une tarification modèle est configurée.",
            })
        finally:
            conn.close()

    def _analytics_window():
        name, start, end = _window(request.args.get("window"))
        return name, start, end

    @app.get("/api/admin/analytics/activity", endpoint="admin_control_analytics_activity")
    @admin_required
    @_with_audit
    def admin_analytics_activity():
        name, start, end = _analytics_window(); conn = get_conn()
        try:
            c = conn.cursor()
            c.execute("SELECT MIN(occurred_at) FROM analytics_events")
            tracked_since = _iso(c.fetchone()[0])
            c.execute("""SELECT
                           COUNT(DISTINCT account_id) FILTER(WHERE event_name IN ('app_opened','session_started') AND occurred_at >= NOW()-INTERVAL '1 day'),
                           COUNT(DISTINCT account_id) FILTER(WHERE event_name IN ('app_opened','session_started') AND occurred_at >= NOW()-INTERVAL '7 days'),
                           COUNT(DISTINCT account_id) FILTER(WHERE event_name IN ('app_opened','session_started') AND occurred_at >= NOW()-INTERVAL '30 days'),
                           COUNT(*) FILTER(WHERE event_name='session_started' AND occurred_at >= %s AND occurred_at < %s)
                        FROM analytics_events""", (start, end))
            row = c.fetchone()
            c.execute("""SELECT occurred_at::date,COUNT(DISTINCT account_id)
                        FROM analytics_events WHERE event_name IN ('app_opened','session_started')
                        AND occurred_at >= %s AND occurred_at < %s GROUP BY 1 ORDER BY 1""", (start, end))
            series = [{"date": str(x[0]), "active_accounts": int(x[1])} for x in c.fetchall()]
            return jsonify({"window": name, "tracked_since": tracked_since,
                            "dau": int(row[0] or 0), "session_starts": int(row[3] or 0),
                            "wau": int(row[1] or 0), "mau": int(row[2] or 0),
                            "wau_mau_status": "first_party_activity_events",
                            "daily_series": series})
        finally: conn.close()

    @app.get("/api/admin/analytics/funnel", endpoint="admin_control_analytics_funnel")
    @admin_required
    @_with_audit
    def admin_analytics_funnel():
        name, start, end = _analytics_window(); conn = get_conn()
        try:
            c = conn.cursor(); events = ("account_created", "onboarding_completed", "free_consultation_started", "free_consultation_used", "paywall_viewed", "premium_purchased")
            counts = {}
            for event in events:
                c.execute("SELECT COUNT(DISTINCT account_id) FROM analytics_events WHERE event_name=%s AND occurred_at >= %s AND occurred_at < %s", (event, start, end)); counts[event] = int(c.fetchone()[0] or 0)
            steps = []; previous = None; first = None
            for event in events:
                count = counts[event]; first = count if first is None else first
                steps.append({"event": event, "accounts": count, "conversion_from_previous": None if previous in (None, 0) else count / previous, "conversion_global": None if not first else count / first}); previous = count
            return jsonify({"window": name, "steps": steps, "tracked_since": _iso(_query_min(conn, "analytics_events", "occurred_at"))})
        finally: conn.close()

    @app.get("/api/admin/analytics/retention", endpoint="admin_control_analytics_retention")
    @admin_required
    @_with_audit
    def admin_analytics_retention():
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute("""WITH cohorts AS (SELECT user_id,(created_at AT TIME ZONE 'Europe/Paris')::date signup_date FROM accounts WHERE deleted_at IS NULL),
              totals AS (SELECT COUNT(*) total FROM cohorts),
              retained AS (SELECT COUNT(*) FILTER(WHERE EXISTS (SELECT 1 FROM analytics_events e WHERE e.account_id=c.user_id AND e.event_name IN ('app_opened','session_started') AND (e.occurred_at AT TIME ZONE 'Europe/Paris')::date=c.signup_date+1)) d1,
                COUNT(*) FILTER(WHERE EXISTS (SELECT 1 FROM analytics_events e WHERE e.account_id=c.user_id AND e.event_name IN ('app_opened','session_started') AND (e.occurred_at AT TIME ZONE 'Europe/Paris')::date=c.signup_date+7)) d7,
                COUNT(*) FILTER(WHERE EXISTS (SELECT 1 FROM analytics_events e WHERE e.account_id=c.user_id AND e.event_name IN ('app_opened','session_started') AND (e.occurred_at AT TIME ZONE 'Europe/Paris')::date=c.signup_date+30)) d30 FROM cohorts c)
              SELECT totals.total,retained.d1,retained.d7,retained.d30 FROM totals,retained""")
            row = c.fetchone(); total = int(row[0] or 0)
            return jsonify({"definition": "retained when an app_opened or session_started event exists on the exact Europe/Paris calendar date J+N.", "tracked_since": _iso(_query_min(conn, "analytics_events", "occurred_at")), "cohort_accounts": total, "d1": None if total == 0 else (row[1] / total), "d7": None if total == 0 else (row[2] / total), "d30": None if total == 0 else (row[3] / total), "historical_status": "available_only_since_first_party_instrumentation"})
        finally: conn.close()

    @app.get("/api/admin/analytics/content", endpoint="admin_control_analytics_content")
    @admin_required
    @_with_audit
    def admin_analytics_content():
        name, start, end = _analytics_window(); conn = get_conn()
        try:
            c = conn.cursor(); c.execute("""SELECT event_name,COALESCE(properties->>'content_type','unknown'),COUNT(*)
                        FROM analytics_events WHERE event_name IN ('content_opened','content_started','content_completed')
                        AND occurred_at >= %s AND occurred_at < %s GROUP BY 1,2 ORDER BY 1,2""", (start,end))
            return jsonify({"window": name, "items": [{"event": x[0], "content_type": x[1], "count": int(x[2])} for x in c.fetchall()], "tracked_since": _iso(_query_min(conn, "analytics_events", "occurred_at"))})
        finally: conn.close()

    @app.get("/api/admin/analytics/wake", endpoint="admin_control_analytics_wake")
    @admin_required
    @_with_audit
    def admin_analytics_wake():
        name, start, end = _analytics_window(); conn = get_conn()
        try:
            c = conn.cursor(); c.execute("SELECT event_name,COUNT(*) FROM analytics_events WHERE event_name IN ('wake_scheduled','wake_triggered','wake_snoozed','wake_stopped') AND occurred_at >= %s AND occurred_at < %s GROUP BY event_name", (start,end))
            return jsonify({"window": name, "events": dict(c.fetchall()), "tracked_since": _iso(_query_min(conn, "analytics_events", "occurred_at"))})
        finally: conn.close()

    @app.get("/api/admin/analytics/acquisition", endpoint="admin_control_analytics_acquisition")
    @admin_required
    @_with_audit
    def admin_analytics_acquisition():
        conn = get_conn()
        try:
            c = conn.cursor(); c.execute("SELECT COALESCE(source,'unknown'),COUNT(*) FROM acquisition_attribution GROUP BY 1 ORDER BY 2 DESC")
            return jsonify({"items": [{"source": x[0], "accounts": int(x[1])} for x in c.fetchall()], "tracked_since": _iso(_query_min(conn, "acquisition_attribution", "captured_at")), "status": "unknown_until_attribution_is_captured"})
        finally: conn.close()


def _query_min(conn, table, column):
    cur = conn.cursor(); cur.execute(f"SELECT MIN({column}) FROM {table}"); return cur.fetchone()[0]
