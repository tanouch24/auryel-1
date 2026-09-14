"""
test_rewards_wallet_api.py — GROS CHANTIER AURYEL (Prompt 2/5) :
    GET  /api/app/rewards/wallet
    POST /api/app/rewards/claim

100 % local : psycopg2 mocké, get_conn -> fausse DB en mémoire (même style de
FakeCursor/dispatch par SOUS-CHAÎNE que test_award_stars.py), resolve_app_session
monkeypatché. Aucun réseau, aucune vraie DB, aucun LLM.
"""

import sys
from datetime import datetime, date, timezone
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

import os
for _k, _v in {
    "SECRET_KEY": "test-secret-key", "VERIFY_TOKEN": "test", "ADMIN_PASSWORD": "test",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "STRIPE_SK": "sk_test_placeholder", "STRIPE_WEBHOOK_SECRET": "whsec_placeholder",
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


NOW = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)
A._utcnow = lambda: NOW

UID1 = "11111111-1111-4111-8111-111111111111"
UID2 = "22222222-2222-4222-8222-222222222222"

DB = {
    "accounts": {},
    "reward_rules": {},
    "reward_wallet": {},
    "daily_action_claims": [],
    "reward_transactions": [],
}


def _default_wallet():
    return {"stars_balance": 0, "current_streak": 0, "best_streak": 0,
            "last_active_reward_date": None}


def _reset_db():
    DB["accounts"].clear()
    DB["reward_rules"].clear()
    DB["reward_wallet"].clear()
    DB["daily_action_claims"].clear()
    DB["reward_transactions"].clear()
    DB["express_products"] = {
        "express_consultation_10min": {"stars_cost": 500, "seconds_granted": 600,
                                       "enabled": True},
    }
    for uid in (UID1, UID2):
        DB["accounts"][uid] = {"user_id": uid, "deleted_at": None}
    # Seed EXACT du rapport (§3) — reflète la migration v49 (mini_game_completed
    # est activée par la migration v50, Prompt 3/5).
    DB["reward_rules"].update({
        "wake_completed":       {"stars_amount": 5,  "enabled": True,  "daily_limit": 1,    "cooldown_seconds": None},
        "daily_card_completed": {"stars_amount": 10, "enabled": True,  "daily_limit": 1,    "cooldown_seconds": None},
        "tarot_completed":      {"stars_amount": 10, "enabled": True,  "daily_limit": 1,    "cooldown_seconds": None},
        "meditation_completed": {"stars_amount": 10, "enabled": True,  "daily_limit": 1,    "cooldown_seconds": None},
        "share_completed":      {"stars_amount": 15, "enabled": True,  "daily_limit": 1,    "cooldown_seconds": None},
        "streak_7_days":        {"stars_amount": 50, "enabled": True,  "daily_limit": None, "cooldown_seconds": None},
        "mini_game_completed":  {"stars_amount": 15, "enabled": True,  "daily_limit": 1,    "cooldown_seconds": None},
        "rewarded_ad_completed": {"stars_amount": 0, "enabled": False, "daily_limit": None, "cooldown_seconds": None},
    })


class FakeCursor:
    def __init__(self):
        self._r = None
        self.rowcount = -1

    def fetchone(self):
        return self._r

    def fetchall(self):
        return self._rows if self._r is None else []

    def close(self):
        pass

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        p = params or ()
        self.rowcount = -1
        self._r = None
        self._rows = []

        if "COALESCE(SUM(seconds_granted), 0) FROM express_consultations" in s:
            self._r = (0,)
            return

        if "FROM accounts WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE" in s:
            row = DB["accounts"].get(p[0])
            self._r = (row["user_id"],) if row and row["deleted_at"] is None else None

        elif "FROM accounts WHERE user_id=%s AND deleted_at IS NULL" in s:
            row = DB["accounts"].get(p[0])
            self._r = (row["user_id"],) if row and row["deleted_at"] is None else None

        elif "SELECT stars_amount, enabled, daily_limit, cooldown_seconds" in s:
            rule = DB["reward_rules"].get(p[0])
            self._r = (
                (rule["stars_amount"], rule["enabled"], rule["daily_limit"],
                 rule["cooldown_seconds"]) if rule else None
            )

        elif "SELECT MAX(created_at) FROM daily_action_claims" in s:
            uid, action_key = p
            matches = [c["created_at"] for c in DB["daily_action_claims"]
                       if c["user_id"] == uid and c["action_key"] == action_key]
            self._r = (max(matches) if matches else None,)

        elif "INSERT INTO reward_wallet (user_id, stars_balance, updated_at)" in s:
            uid = p[0]
            DB["reward_wallet"].setdefault(uid, _default_wallet())

        elif "SELECT stars_balance, current_streak, best_streak, last_active_reward_date" in s:
            uid = p[0]
            w = DB["reward_wallet"].get(uid)
            self._r = (
                (w["stars_balance"], w["current_streak"], w["best_streak"],
                 w["last_active_reward_date"]) if w else None
            )

        elif "SELECT rule_key, stars_amount, daily_limit FROM reward_rules" in s:
            self._rows = sorted(
                [(k, v["stars_amount"], v["daily_limit"])
                 for k, v in DB["reward_rules"].items() if v["enabled"]],
                key=lambda t: t[0],
            )
            self._r = None

        elif "SELECT product_key, stars_cost, seconds_granted FROM express_products" in s:
            self._rows = sorted(
                [(k, v["stars_cost"], v["seconds_granted"])
                 for k, v in DB["express_products"].items() if v["enabled"]],
                key=lambda t: t[1],
            )
            self._r = None

        elif "SELECT delta_stars, balance_after, reason, created_at FROM reward_transactions" in s:
            uid = p[0]
            rows = [t for t in DB["reward_transactions"] if t["user_id"] == uid]
            rows.sort(key=lambda t: t["created_at"], reverse=True)
            self._rows = [
                (t["delta_stars"], t["balance_after"], t["reason"], t["created_at"])
                for t in rows[:20]
            ]
            self._r = None

        elif "SELECT stars_balance FROM reward_wallet WHERE user_id=%s FOR UPDATE" in s:
            uid = p[0]
            w = DB["reward_wallet"].setdefault(uid, _default_wallet())
            self._r = (w["stars_balance"],)

        elif "SELECT COUNT(*) FROM daily_action_claims" in s:
            uid, action_key, claim_date = p
            count = sum(
                1 for c in DB["daily_action_claims"]
                if c["user_id"] == uid and c["action_key"] == action_key
                and c["claim_date"] == claim_date
            )
            self._r = (count,)

        elif "INSERT INTO daily_action_claims" in s:
            (cid, uid, action_key, claim_date, claim_seq, source_id,
             stars_awarded, created_at) = p
            conflict = any(
                c["user_id"] == uid and c["action_key"] == action_key
                and c["claim_date"] == claim_date and c["claim_seq"] == claim_seq
                for c in DB["daily_action_claims"]
            )
            if conflict:
                self._r = None
            else:
                DB["daily_action_claims"].append(dict(
                    id=cid, user_id=uid, action_key=action_key, claim_date=claim_date,
                    claim_seq=claim_seq, source_id=source_id,
                    stars_awarded=stars_awarded, created_at=created_at,
                ))
                self._r = (cid,)

        elif "INSERT INTO reward_transactions" in s and "'earn'" in s:
            (tid, uid, delta, balance_after, reason, source_type, source_id,
             idem, metadata, created_at) = p
            conflict = idem is not None and any(
                t["user_id"] == uid and t.get("idempotency_key") == idem
                for t in DB["reward_transactions"]
            )
            if conflict:
                self._r = None
            else:
                DB["reward_transactions"].append(dict(
                    id=tid, user_id=uid, delta_stars=delta, balance_after=balance_after,
                    type="earn", reason=reason, source_type=source_type,
                    source_id=source_id, idempotency_key=idem, metadata=metadata,
                    created_at=created_at,
                ))
                self._r = (tid,)

        elif "UPDATE reward_wallet SET stars_balance=%s, updated_at=%s" in s:
            new_balance, now, uid = p
            DB["reward_wallet"].setdefault(uid, _default_wallet())
            DB["reward_wallet"][uid]["stars_balance"] = new_balance

        elif ("SELECT current_streak, best_streak, last_active_reward_date "
              "FROM reward_wallet") in s:
            uid = p[0]
            w = DB["reward_wallet"].setdefault(uid, _default_wallet())
            self._r = (w["current_streak"], w["best_streak"],
                       w["last_active_reward_date"])

        elif "UPDATE reward_wallet SET current_streak=%s" in s:
            streak, best, last_date, uid = p
            DB["reward_wallet"].setdefault(uid, _default_wallet())
            DB["reward_wallet"][uid]["current_streak"] = streak
            DB["reward_wallet"][uid]["best_streak"] = best
            DB["reward_wallet"][uid]["last_active_reward_date"] = last_date

        elif "SELECT stars_balance FROM reward_wallet WHERE user_id=%s" in s:
            uid = p[0]
            w = DB["reward_wallet"].get(uid)
            self._r = (w["stars_balance"] if w else 0,)

        else:
            raise AssertionError("SQL non modélisé (rewards wallet api) : " + s)


class FakeConn:
    def cursor(self):
        return FakeCursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


A.get_conn = lambda: FakeConn()
A.app.config["TESTING"] = True
try:
    A.limiter.enabled = False
except Exception:
    pass
client = A.app.test_client()

_CURRENT = {"uid": UID1}
A.resolve_app_session = lambda tok: (
    {"session_id": "s", "user_id": _CURRENT["uid"], "email": "x@x.co"} if tok else None
)


def _as(uid):
    _CURRENT["uid"] = uid


def _hdr():
    return {"Authorization": "Bearer x"}


def _get_wallet():
    return client.get("/api/app/rewards/wallet", headers=_hdr())


def _claim(action_key):
    return client.post("/api/app/rewards/claim", json={"action_key": action_key},
                        headers=_hdr())


# ===========================================================================
# 0. Routes existent aux chemins EXACTS attendus par Flutter (manifeste)
# ===========================================================================
_rules = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
check(("/api/app/rewards/wallet", "GET") in _rules,
      "0a GET /api/app/rewards/wallet enregistrée")
check(("/api/app/rewards/claim", "POST") in _rules,
      "0b POST /api/app/rewards/claim enregistrée")

# ===========================================================================
# 1. Wallet — nouveau compte = 0
# ===========================================================================
print("=" * 64)
print("1. Wallet — nouveau compte")
print("=" * 64)

_reset_db()
_as(UID1)
res = _get_wallet()
body = res.get_json()
check(res.status_code == 200, "1a 200 OK")
check(body["stars_balance"] == 0, "1b stars_balance = 0 (nouveau compte)")
check(body["streak"]["current_streak"] == 0 and body["streak"]["best_streak"] == 0,
      "1c streak à 0")
check(body["recent_transactions"] == [], "1d historique vide")
check(body["minutes_converted_this_month"] == 0
      and body["monthly_minutes_limit"] == 30
      and body["monthly_minutes_remaining"] == 30,
      "1e progression mensuelle des conversions Étoiles exposée")

# ===========================================================================
# 2. Wallet — seules les règles ACTIVES apparaissent
# ===========================================================================
print("=" * 64)
print("2. Wallet — règles actives uniquement")
print("=" * 64)

rule_keys = {r["rule_key"] for r in body["rules"]}
check(rule_keys == {
    "wake_completed", "daily_card_completed", "tarot_completed",
    "meditation_completed", "share_completed", "streak_7_days",
    "mini_game_completed",
}, f"2a exactement les 7 règles actives (mini_game_completed activée par la "
   f"migration v50, Prompt 3/5), aucune future désactivée ({rule_keys})")
check("rewarded_ad_completed" not in rule_keys,
      "2b rewarded_ad_completed JAMAIS exposée (enabled=False)")
amounts = {r["rule_key"]: r["stars_amount"] for r in body["rules"]}
check(amounts["wake_completed"] == 5 and amounts["daily_card_completed"] == 10
      and amounts["tarot_completed"] == 10 and amounts["meditation_completed"] == 10
      and amounts["share_completed"] == 15 and amounts["streak_7_days"] == 50
      and amounts["mini_game_completed"] == 15,
      "2c montants exacts du rapport")
limits = {r["rule_key"]: r["daily_limit"] for r in body["rules"]}
check(limits["wake_completed"] == 1 and limits["daily_card_completed"] == 1
      and limits["tarot_completed"] == 1 and limits["meditation_completed"] == 1
      and limits["share_completed"] == 1 and limits["mini_game_completed"] == 1
      and limits["streak_7_days"] is None,
      f"2c bis daily_limit exposé tel quel depuis reward_rules, jamais "
      f"inventé côté Flutter ({limits})")
check(
    body["express_products"] == [
        {"product_key": "express_consultation_10min", "stars_cost": 500,
         "seconds_granted": 600}
    ],
    f"2d express_products expose UNIQUEMENT le catalogue actif, coût/durée "
    f"résolus serveur ({body['express_products']})",
)

# ===========================================================================
# 3. Wallet — modification serveur du montant reflétée SANS nouvelle app
# ===========================================================================
print("=" * 64)
print("3. Montant modifiable côté serveur uniquement")
print("=" * 64)

DB["reward_rules"]["wake_completed"]["stars_amount"] = 7
res = _get_wallet()
check(res.get_json()["rules"] and
      next(r["stars_amount"] for r in res.get_json()["rules"]
           if r["rule_key"] == "wake_completed") == 7,
      "3a un ajustement en base (7 au lieu de 5) est reflété immédiatement, "
      "sans rien changer côté Flutter")
DB["reward_rules"]["wake_completed"]["stars_amount"] = 5  # remis à l'état nominal

# ===========================================================================
# 4. Wallet — authentification requise
# ===========================================================================
print("=" * 64)
print("4. Authentification")
print("=" * 64)

res_noauth = client.get("/api/app/rewards/wallet")
check(res_noauth.status_code == 401, "4a GET sans Bearer -> 401")

DB["accounts"][UID1]["deleted_at"] = NOW
res_deleted = _get_wallet()
check(res_deleted.status_code == 401, "4b compte supprimé -> 401")
DB["accounts"][UID1]["deleted_at"] = None

# ===========================================================================
# 5. Claim — whitelist stricte
# ===========================================================================
print("=" * 64)
print("5. POST /claim — whitelist stricte de action_key")
print("=" * 64)

_reset_db()
_as(UID1)
res = _claim("wake_completed")
check(res.status_code == 200 and res.get_json()["awarded"] is True,
      "5a wake_completed (whitelisté) -> 200, awarded=True")
check(res.get_json()["stars_awarded"] == 5,
      "5b montant résolu depuis reward_rules (5), jamais fourni par le body")

for banned in ("tarot_completed", "meditation_completed", "daily_card_completed",
               "share_completed", "streak_7_days", "n_importe_quoi", ""):
    res = _claim(banned)
    check(res.status_code == 400,
          f"5c action_key='{banned}' hors whitelist -> 400 (ces règles sont "
          "créditées UNIQUEMENT depuis leurs points d'action serveur réels, "
          "jamais via cet endpoint générique)")

res_no_amount = client.post("/api/app/rewards/claim",
                             json={"action_key": "wake_completed", "stars_amount": 99999},
                             headers=_hdr())
check(res_no_amount.get_json()["stars_awarded"] != 99999,
      "5d un `stars_amount` ajouté au body par le client est IGNORÉ — aucun "
      "montant client n'est jamais lu")

# ===========================================================================
# 6. Claim — anti-farming quotidien via l'API
# ===========================================================================
print("=" * 64)
print("6. Anti-farming via HTTP")
print("=" * 64)

res2 = _claim("wake_completed")
check(res2.status_code == 200 and res2.get_json()["awarded"] is False,
      "6a 2e réclamation le même jour (même utilisateur) -> awarded=False, "
      "toujours 200 (ce n'est pas une erreur, juste rien à créditer)")
check(res2.get_json()["new_balance"] == 5, "6b solde inchangé (5, pas 10)")

# ===========================================================================
# 7. Claim — 2 comptes distincts n'interfèrent jamais
# ===========================================================================
print("=" * 64)
print("7. Isolation entre comptes")
print("=" * 64)

_as(UID2)
res3 = _claim("wake_completed")
check(res3.get_json()["awarded"] is True,
      "7a UID2 réclame normalement le même jour (pas affecté par UID1)")
_as(UID1)
w1 = _get_wallet().get_json()
_as(UID2)
w2 = _get_wallet().get_json()
check(w1["stars_balance"] == 5 and w2["stars_balance"] == 5,
      "7b chaque compte a son PROPRE solde (5 chacun)")

print("-" * 64)
total = _STATE["pass"] + _STATE["fail"]
print(f"RESULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko (sur {total})")
sys.exit(1 if _STATE["fail"] else 0)
