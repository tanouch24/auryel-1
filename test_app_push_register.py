"""
test_app_push_register.py — POST /api/app/push/register + /unregister.

100 % local : psycopg2 mocké, get_conn -> fake DB en mémoire (`accounts`,
`push_devices`). resolve_app_session monkeypatché. Aucun réseau, aucun FCM.
Style aligné sur test_app_support.py.
"""

import sys
import inspect
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

import os
for _k, _v in {
    "SECRET_KEY": "test", "VERIFY_TOKEN": "test", "ADMIN_PASSWORD": "test",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "STRIPE_SK": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "WHATSAPP_TOKEN": "test", "PHONE_NUMBER_ID": "test", "GROQ_API_KEY": "test",
    "RESEND_API_KEY": "test", "META_APP_SECRET": "test", "DAILY_SECRET": "test",
    "SEO_SECRET": "test",
}.items():
    os.environ.setdefault(_k, _v)

import auryel_bot as A

_STATE = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _STATE["pass"] += 1
        print(f"OK  {label}")
    else:
        _STATE["fail"] += 1
        print(f"XX  {label}")


UID1 = "11111111-1111-4111-8111-111111111111"
UID2 = "22222222-2222-4222-8222-222222222222"

_ACCOUNTS = {}
_DEVICES = {}   # fcm_token -> dict


def _reset():
    _ACCOUNTS.clear()
    _DEVICES.clear()
    _ACCOUNTS[UID1] = {"deleted_at": None}
    _ACCOUNTS[UID2] = {"deleted_at": None}


class _Cur:
    def __init__(self):
        self._one = None
        self.rowcount = 0

    def fetchone(self):
        return self._one

    def fetchall(self):
        return []

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = " ".join(sql.split())
        if k.startswith("SELECT 1 FROM accounts WHERE user_id=%s AND deleted_at IS NULL"):
            acc = _ACCOUNTS.get(params[0])
            self._one = (1,) if (acc and acc["deleted_at"] is None) else None
        elif k.startswith("INSERT INTO push_devices"):
            (_id, user_id, fcm_token, platform, app_version, os_version,
             device_label, created_at, updated_at, last_seen_at) = params
            row = _DEVICES.get(fcm_token)
            if row is None:
                _DEVICES[fcm_token] = {
                    "id": _id, "user_id": user_id, "fcm_token": fcm_token,
                    "platform": platform, "app_version": app_version,
                    "os_version": os_version, "device_label": device_label,
                    "enabled": True, "revoked_at": None, "invalid_at": None,
                }
                self._one = (_id,)
            else:
                row.update(user_id=user_id, platform=platform,
                           app_version=app_version, os_version=os_version,
                           device_label=device_label, enabled=True,
                           revoked_at=None, invalid_at=None)
                self._one = (row["id"],)
            self.rowcount = 1
        elif k.startswith("UPDATE push_devices SET enabled=FALSE, updated_at=%s WHERE fcm_token=%s AND user_id=%s"):
            _now, fcm_token, user_id = params
            row = _DEVICES.get(fcm_token)
            if row is not None and row["user_id"] == user_id:
                row["enabled"] = False
                self.rowcount = 1
            else:
                self.rowcount = 0
        else:
            raise AssertionError("SQL non géré par le fake push : " + k)


class _Conn:
    def cursor(self):
        return _Cur()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


A.get_conn = lambda: _Conn()
A.app.config["TESTING"] = True
try:
    A.limiter.enabled = False
except Exception:
    pass
client = A.app.test_client()

_CURRENT = {"uid": UID1}
A.resolve_app_session = lambda tok: (
    {"session_id": "s", "user_id": _CURRENT["uid"], "email": "s@auryel.co"}
    if tok else None
)


def _as(uid):
    _CURRENT["uid"] = uid


def _reg(body, tok="x"):
    hdr = {"Authorization": f"Bearer {tok}"} if tok else {}
    return client.post("/api/app/push/register", headers=hdr, json=body)


def _unreg(body, tok="x"):
    hdr = {"Authorization": f"Bearer {tok}"} if tok else {}
    return client.post("/api/app/push/unregister", headers=hdr, json=body)


TOK_A = "device-token-aaaaaaaaaaaaaaaaaaaa"
TOK_B = "device-token-bbbbbbbbbbbbbbbbbbbb"

# --- 0. routes + garde-fous ---------------------------------------------
_rules = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
check(("/api/app/push/register", "POST") in _rules, "0a register enregistrée")
check(("/api/app/push/unregister", "POST") in _rules, "0b unregister enregistrée")
_src = inspect.getsource(A.api_app_push_register)
check('g.app_account["user_id"]' in _src, "0c identité = Bearer")
check('data.get("user_id")' not in _src, "0d aucun user_id lu du body")
check('@limiter.limit("30 per hour")' in _src, "0e rate limit présent")

# --- 1. auth obligatoire ------------------------------------------------
_reset()
check(_reg({"fcm_token": TOK_A}, tok=None).status_code == 401, "1 sans Bearer -> 401")

# --- 2. token obligatoire + bornes -----------------------------------
_reset(); _as(UID1)
check(_reg({}).status_code == 400, "2a token absent -> 400")
check(_reg({"fcm_token": "   "}).status_code == 400, "2b token vide -> 400")
check(_reg({"fcm_token": "x" * (A._PUSH_TOKEN_MAX + 1)}).status_code == 400,
      "2c token trop long -> 400")
check(_reg("pas un objet").status_code == 400, "2d body non-objet -> 400")

# --- 3. register OK -> 200 {status:registered} ----------------------
_reset(); _as(UID1)
r = _reg({"fcm_token": TOK_A, "platform": "android", "app_version": "1.0.0",
          "os_version": "Android 14", "device_label": "Galaxy A07"})
check(r.status_code == 200 and r.get_json() == {"status": "registered"},
      "3a register -> 200 {status: registered}")
check(_DEVICES[TOK_A]["user_id"] == UID1 and _DEVICES[TOK_A]["enabled"] is True,
      "3b device rattaché au compte, enabled")
check("registered" not in str(r.get_json()).lower().replace("registered", "")
      or TOK_A not in r.get_data(as_text=True),
      "3c le jeton n'apparaît pas dans la réponse")

# --- 4. plateforme hors allowlist -> repli 'android' ---------------
_reset(); _as(UID1)
_reg({"fcm_token": TOK_A, "platform": "windows"})
check(_DEVICES[TOK_A]["platform"] == "android", "4 plateforme inconnue -> 'android'")

# --- 5. idempotence : 2e register même token -> toujours 200, 1 ligne
_reset(); _as(UID1)
_reg({"fcm_token": TOK_A})
r = _reg({"fcm_token": TOK_A})
check(r.status_code == 200 and len(_DEVICES) == 1, "5 register idempotent (upsert)")

# --- 6. réaffectation : téléphone change de compte -----------------
_reset(); _as(UID1)
_reg({"fcm_token": TOK_A})
_as(UID2)
_reg({"fcm_token": TOK_A})
check(_DEVICES[TOK_A]["user_id"] == UID2 and len(_DEVICES) == 1,
      "6 même jeton réaffecté au nouveau compte (pas de doublon)")

# --- 7. compte supprimé -> 401 -----------------------------------
_reset(); _as(UID1)
_ACCOUNTS[UID1]["deleted_at"] = "2026-01-01"
check(_reg({"fcm_token": TOK_A}).status_code == 401, "7 compte supprimé -> 401")

# --- 8. unregister : idempotent, ne touche QUE le token du compte -----
_reset(); _as(UID1)
_reg({"fcm_token": TOK_A})
_reg({"fcm_token": TOK_B})
r = _unreg({"fcm_token": TOK_A})
check(r.status_code == 200 and r.get_json() == {"status": "unregistered"},
      "8a unregister -> 200 {status: unregistered}")
check(_DEVICES[TOK_A]["enabled"] is False and _DEVICES[TOK_B]["enabled"] is True,
      "8b unregister ne désactive QUE le jeton visé (autres appareils intacts)")

# --- 9. unregister d'un token inconnu -> 200 (idempotent) --------
_reset(); _as(UID1)
check(_unreg({"fcm_token": "jamais-vu"}).status_code == 200,
      "9 unregister token inconnu -> 200")

# --- 10. unregister ne peut pas désactiver le token d'un AUTRE compte
_reset(); _as(UID1)
_reg({"fcm_token": TOK_A})
_as(UID2)
_unreg({"fcm_token": TOK_A})
check(_DEVICES[TOK_A]["enabled"] is True,
      "10 unregister d'un autre compte -> sans effet (token pas au compte courant)")

# --- 11. token absent pour unregister -> 400 --------------------
_reset(); _as(UID1)
check(_unreg({}).status_code == 400, "11 unregister sans token -> 400")

# --- 12. tables purgées à la suppression de compte -------------
check("push_devices" in A._ACCOUNT_DELETE_CHILD_TABLES
      and "notification_sends" in A._ACCOUNT_DELETE_CHILD_TABLES,
      "12 push_devices + notification_sends dans _ACCOUNT_DELETE_CHILD_TABLES")

print(f"\n{_STATE['pass']} OK / {_STATE['fail']} XX")
raise SystemExit(1 if _STATE["fail"] else 0)
