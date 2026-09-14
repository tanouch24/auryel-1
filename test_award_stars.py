"""
test_award_stars.py — GROS CHANTIER AURYEL (Prompt 2/5) : primitive centrale
`award_stars` / `_award_stars_tx` / `_bump_streak_tx` / `debit_stars`
(fondation ÉTOILES).

100 % local : psycopg2 mocké, FakeConn/FakeCursor en mémoire (même style que
test_bonus_credit.py). Aucune vraie DB, aucun réseau, aucun LLM.
"""

import sys
from datetime import datetime, date, timedelta, timezone
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


NOW = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)  # 12h Europe/Paris (été)
A._utcnow = lambda: NOW

UID = "11111111-1111-4111-8111-111111111111"
UID2 = "22222222-2222-4222-8222-222222222222"

DB = {
    "accounts": {},
    "reward_rules": {},
    "reward_wallet": {},
    "daily_action_claims": [],
    "reward_transactions": [],
}


def reset_db():
    DB["accounts"].clear()
    DB["reward_rules"].clear()
    DB["reward_wallet"].clear()
    DB["daily_action_claims"].clear()
    DB["reward_transactions"].clear()


def seed_account(uid, deleted=False):
    DB["accounts"][uid] = {"user_id": uid, "deleted_at": (NOW if deleted else None)}


def seed_rule(rule_key, stars_amount, enabled=True, daily_limit=1, cooldown_seconds=None):
    DB["reward_rules"][rule_key] = {
        "stars_amount": stars_amount, "enabled": enabled,
        "daily_limit": daily_limit, "cooldown_seconds": cooldown_seconds,
    }


def _default_wallet():
    return {"stars_balance": 0, "current_streak": 0, "best_streak": 0,
            "last_active_reward_date": None}


def seed_all_rules():
    seed_rule("wake_completed", 5)
    seed_rule("daily_card_completed", 10)
    seed_rule("tarot_completed", 10)
    seed_rule("meditation_completed", 10)
    seed_rule("share_completed", 15)
    seed_rule("streak_7_days", 50, daily_limit=None)


class FakeCursor:
    def __init__(self):
        self._r = None
        self.rowcount = -1

    def fetchone(self):
        return self._r

    def close(self):
        pass

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        p = params or ()
        self.rowcount = -1
        self._r = None

        if "FROM accounts WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE" in s:
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

        elif "SELECT stars_balance FROM reward_wallet WHERE user_id=%s FOR UPDATE" in s:
            uid = p[0]
            w = DB["reward_wallet"].setdefault(uid, _default_wallet())
            self._r = (w["stars_balance"],)

        elif "INSERT INTO daily_action_claims" in s:
            (cid, uid, action_key, claim_date, source_id, stars_awarded, created_at) = p
            conflict = any(
                c["user_id"] == uid and c["action_key"] == action_key
                and c["claim_date"] == claim_date
                for c in DB["daily_action_claims"]
            )
            if conflict:
                self._r = None
            else:
                DB["daily_action_claims"].append(dict(
                    id=cid, user_id=uid, action_key=action_key, claim_date=claim_date,
                    source_id=source_id, stars_awarded=stars_awarded, created_at=created_at,
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

        elif "INSERT INTO reward_transactions" in s and "'spend'" in s:
            (tid, uid, delta, balance_after, reason, idem, created_at) = p
            conflict = idem is not None and any(
                t["user_id"] == uid and t.get("idempotency_key") == idem
                for t in DB["reward_transactions"]
            )
            if conflict:
                self._r = None
            else:
                DB["reward_transactions"].append(dict(
                    id=tid, user_id=uid, delta_stars=delta, balance_after=balance_after,
                    type="spend", reason=reason, source_type=None, source_id=None,
                    idempotency_key=idem, metadata=None, created_at=created_at,
                ))
                self._r = (tid,)

        elif "SELECT balance_after FROM reward_transactions" in s:
            uid, idem = p
            match = next(
                (t for t in DB["reward_transactions"]
                 if t["user_id"] == uid and t.get("idempotency_key") == idem),
                None,
            )
            self._r = (match["balance_after"],) if match else None

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
            raise AssertionError("SQL non modélisé (award_stars) : " + s)


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

# ---------------------------------------------------------------------------
# 1. Crédit simple — le montant vient TOUJOURS du serveur (reward_rules)
# ---------------------------------------------------------------------------
print("=" * 64)
print("1. Crédit simple")
print("=" * 64)

reset_db()
seed_account(UID)
seed_all_rules()
r = A.award_stars(UID, "meditation_completed", idempotency_key="meditation_completed:u1:2026-09-09")
check(r["awarded"] is True, "1a awarded=True au 1er crédit")
check(r["stars_awarded"] == 10, "1b montant = 10 (résolu depuis reward_rules, jamais fourni par l'appelant)")
check(r["new_balance"] == 10, "1c solde = 10 après crédit")
check(DB["reward_wallet"][UID]["stars_balance"] == 10, "1d wallet réellement mis à jour")
check(len(DB["reward_transactions"]) == 1 and DB["reward_transactions"][0]["delta_stars"] == 10
      and DB["reward_transactions"][0]["balance_after"] == 10
      and DB["reward_transactions"][0]["type"] == "earn"
      and DB["reward_transactions"][0]["reason"] == "meditation_completed",
      "1e une ligne reward_transactions correcte (type=earn, balance_after exact)")
check(len(DB["daily_action_claims"]) == 1, "1f une ligne daily_action_claims (daily_limit=1)")

# ---------------------------------------------------------------------------
# 2. Règle DÉSACTIVÉE -> jamais de crédit
# ---------------------------------------------------------------------------
print("=" * 64)
print("2. Règle désactivée (enabled=False)")
print("=" * 64)

reset_db()
seed_account(UID)
seed_rule("mini_game_completed", 0, enabled=False)
r = A.award_stars(UID, "mini_game_completed", idempotency_key="mini_game_completed:u1:2026-09-09")
check(r["awarded"] is False and r["reason"] == "rule_disabled",
      "2a règle désactivée -> awarded=False, reason=rule_disabled")
check(r["new_balance"] == 0, "2b solde inchangé (0)")
check(len(DB["reward_transactions"]) == 0, "2c aucune transaction écrite")

# ---------------------------------------------------------------------------
# 3. Règle INCONNUE
# ---------------------------------------------------------------------------
print("=" * 64)
print("3. Règle inconnue")
print("=" * 64)

reset_db()
seed_account(UID)
r = A.award_stars(UID, "does_not_exist", idempotency_key="x:u1:2026-09-09")
check(r["awarded"] is False and r["reason"] == "unknown_rule",
      "3a règle inconnue -> awarded=False, reason=unknown_rule, aucun crash")

# ---------------------------------------------------------------------------
# 4. AUCUN montant fourni par l'appelant -> toujours celui du serveur
# ---------------------------------------------------------------------------
print("=" * 64)
print("4. Le montant vient TOUJOURS du serveur")
print("=" * 64)

reset_db()
seed_account(UID)
seed_rule("wake_completed", 5)
r = A.award_stars(UID, "wake_completed", idempotency_key="wake_completed:u1:2026-09-09")
check(r["stars_awarded"] == 5,
      "4a le montant crédité est EXACTEMENT reward_rules.stars_amount (5), "
      "aucun paramètre `stars_amount` n'existe dans la signature de award_stars")
import inspect as _inspect
_sig = _inspect.signature(A.award_stars)
check("stars_amount" not in _sig.parameters and "amount" not in _sig.parameters
      and "delta" not in _sig.parameters,
      "4b signature award_stars() ne contient AUCUN paramètre de montant "
      f"(paramètres réels : {list(_sig.parameters)})")

# ---------------------------------------------------------------------------
# 5. Anti-farming quotidien — daily_action_claims
# ---------------------------------------------------------------------------
print("=" * 64)
print("5. Anti-farming quotidien (même jour = 1 seule récompense)")
print("=" * 64)

reset_db()
seed_account(UID)
seed_all_rules()
r1 = A.award_stars(UID, "tarot_completed", idempotency_key="tarot_completed:u1:2026-09-09")
check(r1["awarded"] is True, "5a 1re réclamation du jour -> awarded=True")
r2 = A.award_stars(UID, "tarot_completed", idempotency_key="tarot_completed:u1:2026-09-09")
check(r2["awarded"] is False and r2["reason"] in ("daily_limit_reached", "idempotency_conflict"),
      "5b même jour, même clé -> awarded=False (jamais un 2e crédit)")
check(DB["reward_wallet"][UID]["stars_balance"] == 10,
      "5c solde INCHANGÉ après la 2e tentative (toujours 10, pas 20)")
# Rejeu avec une clé DIFFÉRENTE (ex. retry réseau générant un autre uuid côté
# client bugué) : daily_action_claims protège quand même (clé de conflit =
# jour calendaire, pas la clé d'idempotence).
r3 = A.award_stars(UID, "tarot_completed", idempotency_key="tarot_completed:u1:RETRY-DIFFERENT")
check(r3["awarded"] is False and r3["reason"] == "daily_limit_reached",
      "5d même jour, clé DIFFÉRENTE -> daily_action_claims bloque quand même "
      "(fermer/réouvrir l'app, multi-device, retry ne peuvent jamais contourner "
      "l'anti-farming)")
check(DB["reward_wallet"][UID]["stars_balance"] == 10, "5e solde toujours 10")
check(len({c["id"] for c in DB["daily_action_claims"]}) == 1,
      "5f une SEULE ligne daily_action_claims au total pour ce jour/action")

# ---------------------------------------------------------------------------
# 6. Lendemain -> nouvelle récompense
# ---------------------------------------------------------------------------
print("=" * 64)
print("6. Lendemain -> nouvelle récompense")
print("=" * 64)

TOMORROW = NOW + timedelta(days=1)
A._utcnow = lambda: TOMORROW
r = A.award_stars(UID, "tarot_completed", idempotency_key="tarot_completed:u1:2026-09-10")
check(r["awarded"] is True, "6a jour suivant -> awarded=True")
check(DB["reward_wallet"][UID]["stars_balance"] == 20, "6b solde cumulé 10 + 10 = 20")
A._utcnow = lambda: NOW  # remise à l'heure pour la suite

# ---------------------------------------------------------------------------
# 7. Idempotence — même clé rejouée sur une règle SANS daily_limit
#    (streak_7_days, jamais gardée par daily_action_claims)
# ---------------------------------------------------------------------------
print("=" * 64)
print("7. Idempotence pure (règle sans daily_limit)")
print("=" * 64)

reset_db()
seed_account(UID)
seed_all_rules()
r1 = A.award_stars(UID, "streak_7_days", idempotency_key="streak_7_days:u1:1")
check(r1["awarded"] is True, "7a 1er crédit du jalon streak")
check(len(DB["daily_action_claims"]) == 0,
      "7b AUCUNE ligne daily_action_claims (daily_limit=None pour streak_7_days)")
r2 = A.award_stars(UID, "streak_7_days", idempotency_key="streak_7_days:u1:1")
check(r2["awarded"] is False and r2["reason"] == "idempotency_conflict",
      "7c rejeu de la MÊME clé -> awarded=False, reason=idempotency_conflict "
      "(seule protection possible ici : daily_action_claims ne s'applique pas)")
check(DB["reward_wallet"][UID]["stars_balance"] == 50, "7d solde inchangé (50, pas 100)")
r3 = A.award_stars(UID, "streak_7_days", idempotency_key="streak_7_days:u1:2")
check(r3["awarded"] is True and DB["reward_wallet"][UID]["stars_balance"] == 100,
      "7e clé DIFFÉRENTE (jalon suivant) -> nouveau crédit cumulatif")

# ---------------------------------------------------------------------------
# 8. Concurrence — deux comptes distincts n'entrent jamais en collision
# ---------------------------------------------------------------------------
print("=" * 64)
print("8. Scoping par utilisateur")
print("=" * 64)

reset_db()
seed_account(UID)
seed_account(UID2)
seed_all_rules()
A.award_stars(UID, "share_completed", idempotency_key="share_completed:u1:2026-09-09")
r = A.award_stars(UID2, "share_completed", idempotency_key="share_completed:u1:2026-09-09")
check(r["awarded"] is True,
      "8a MÊME clé littérale mais AUTRE user_id -> pas un conflit (scopé par user_id)")
check(DB["reward_wallet"][UID]["stars_balance"] == 15
      and DB["reward_wallet"][UID2]["stars_balance"] == 15,
      "8b chaque compte crédité indépendamment (15 chacun)")

# ---------------------------------------------------------------------------
# 9. Compte inconnu / supprimé -> jamais de crédit, jamais de crash
# ---------------------------------------------------------------------------
print("=" * 64)
print("9. Comptes invalides")
print("=" * 64)

r_unknown = A.award_stars("00000000-0000-0000-0000-000000000000", "wake_completed",
                           idempotency_key="k1")
check(r_unknown["awarded"] is False and r_unknown["reason"] == "unknown_account",
      "9a compte inconnu -> awarded=False, reason=unknown_account")

reset_db()
seed_account(UID, deleted=True)
seed_all_rules()
r_deleted = A.award_stars(UID, "wake_completed", idempotency_key="k2")
check(r_deleted["awarded"] is False and r_deleted["reason"] == "unknown_account",
      "9b compte supprimé -> awarded=False, aucun crédit")

# ---------------------------------------------------------------------------
# 10. Garde-fous idempotency_key
# ---------------------------------------------------------------------------
print("=" * 64)
print("10. Garde-fous")
print("=" * 64)

reset_db()
seed_account(UID)
seed_all_rules()
_raised = False
try:
    A.award_stars(UID, "wake_completed", idempotency_key="")
except ValueError:
    _raised = True
check(_raised, "10a idempotency_key vide -> ValueError (jamais un crédit non idempotent)")

_raised = False
try:
    A.award_stars(UID, "wake_completed", idempotency_key=None)
except ValueError:
    _raised = True
check(_raised, "10b idempotency_key=None -> ValueError")

print("-" * 64)
print("11. Streak — jours actifs consécutifs (jour Europe/Paris)")
print("=" * 64)


def _award_on(uid, day_dt, rule_key="wake_completed"):
    A._utcnow = lambda: day_dt
    day = A._wellbeing_day(day_dt)
    return A.award_stars(uid, rule_key,
                          idempotency_key=f"{rule_key}:{uid}:{day}", now=day_dt)


reset_db()
seed_account(UID)
seed_all_rules()

D0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
r = _award_on(UID, D0)
check(r["awarded"] is True, "11a jour 1 : awarded=True")
check(DB["reward_wallet"][UID]["current_streak"] == 1, "11b streak = 1 après le 1er jour")

D1 = D0 + timedelta(days=1)
_award_on(UID, D1)
check(DB["reward_wallet"][UID]["current_streak"] == 2, "11c jour consécutif -> streak = 2")

D2 = D1 + timedelta(days=1)
_award_on(UID, D2)
check(DB["reward_wallet"][UID]["current_streak"] == 3, "11d 3e jour consécutif -> streak = 3")
check(DB["reward_wallet"][UID]["best_streak"] == 3, "11e best_streak suit current_streak")

# Trou d'un jour (D2 -> D4, D3 sauté) -> reset à 1 (aujourd'hui = jour 1).
D4 = D2 + timedelta(days=2)
_award_on(UID, D4)
check(DB["reward_wallet"][UID]["current_streak"] == 1,
      "11f trou d'un jour -> reset à 1 (jamais 0, jamais une simple poursuite)")
check(DB["reward_wallet"][UID]["best_streak"] == 3,
      "11g best_streak CONSERVE le record précédent (3) malgré le reset")

# 2 actions ÉLIGIBLES le MÊME jour -> une seule incrémentation.
r2 = _award_on(UID, D4 + timedelta(hours=2), rule_key="daily_card_completed")
check(r2["awarded"] is True, "11h 2e action du même jour créditée normalement (Étoiles)")
check(DB["reward_wallet"][UID]["current_streak"] == 1,
      "11i ...mais le streak N'EST PAS incrémenté 2 fois le même jour")

print("-" * 64)
print("12. Jalon de streak — 7e jour = +50 ⭐, jamais deux fois")
print("=" * 64)

reset_db()
seed_account(UID)
seed_all_rules()
base = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
for i in range(7):
    _award_on(UID, base + timedelta(days=i))
check(DB["reward_wallet"][UID]["current_streak"] == 7, "12a 7 jours consécutifs -> streak = 7")
# 7 crédits wake_completed (5 chacun = 35) + 1 crédit streak_7_days (50) = 85.
check(DB["reward_wallet"][UID]["stars_balance"] == 7 * 5 + 50,
      "12b le jalon +50 est bien crédité UNE fois au 7e jour "
      f"(solde = {DB['reward_wallet'][UID]['stars_balance']})")
check(
    sum(1 for t in DB["reward_transactions"] if t["reason"] == "streak_7_days") == 1,
    "12c EXACTEMENT une transaction 'streak_7_days' au total",
)
# Un 8e jour ne doit PAS re-décelencher le jalon (14e jour seulement).
_award_on(UID, base + timedelta(days=7))
check(
    sum(1 for t in DB["reward_transactions"] if t["reason"] == "streak_7_days") == 1,
    "12d 8e jour (streak=8) -> toujours UNE seule transaction streak_7_days "
    "(le prochain jalon est le 14e jour)",
)

print("-" * 64)
print("13. debit_stars — fondation NON exposée (Prompt 3/5), jamais de solde négatif")
print("=" * 64)

reset_db()
seed_account(UID)
seed_all_rules()
A.award_stars(UID, "wake_completed", idempotency_key="wake_completed:u1:d")
d1 = A.debit_stars(UID, 3, "test_debit", idempotency_key="debit:u1:1")
check(d1["debited"] is True and d1["new_balance"] == 2,
      "13a débit simple : 5 - 3 = 2")
d2 = A.debit_stars(UID, 100, "test_debit", idempotency_key="debit:u1:2")
check(d2["debited"] is False and d2["reason"] == "insufficient_balance"
      and d2["new_balance"] == 2,
      "13b débit > solde -> refusé, JAMAIS de solde négatif, solde inchangé")
d3 = A.debit_stars(UID, 3, "test_debit", idempotency_key="debit:u1:1")
check(d3["debited"] is True and d3["new_balance"] == 2,
      "13c rejeu de la MÊME clé -> renvoie le résultat ORIGINAL (succès, "
      "solde déjà débité) — JAMAIS 'insufficient_balance' à tort sur un "
      "simple rejeu d'un débit qui avait réussi")
check(
    sum(1 for t in DB["reward_transactions"] if t["type"] == "spend") == 1,
    "13d UNE seule transaction 'spend' malgré le rejeu",
)

print("-" * 64)
total = _STATE["pass"] + _STATE["fail"]
print(f"RESULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko (sur {total})")
sys.exit(1 if _STATE["fail"] else 0)
