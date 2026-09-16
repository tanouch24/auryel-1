"""
test_bonus_credit.py — GROS CHANTIER ÉCONOMIQUE (Prompt 1/5) :
`credit_bonus_time` / `_credit_bonus_time_tx` (primitive générique et
idempotente de crédit du bucket `bonus`, fondation pour les prochains
chantiers — aucun appelant HTTP dans ce lot).

100 % local : psycopg2 mocké, FakeConn/FakeCursor en mémoire (même style que
test_billing_extra_hour.py). Aucune vraie DB, aucun réseau, aucun LLM.
"""

import sys
from datetime import datetime, timezone
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


NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
A._utcnow = lambda: NOW

UID = "11111111-1111-4111-8111-111111111111"
UID2 = "22222222-2222-4222-8222-222222222222"

DB = {"accounts": {}, "time_ledger": []}


def reset_db():
    DB["accounts"].clear()
    DB["time_ledger"].clear()


def seed_account(uid, deleted=False, earned=0):
    DB["accounts"][uid] = {
        "user_id": uid, "deleted_at": (NOW if deleted else None),
        "earned_seconds_remaining": earned,
    }


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

        elif "INSERT INTO time_ledger" in s:
            (lid, uid, amount, reason, key, created_at) = p
            conflict = key is not None and any(
                e["user_id"] == uid and e.get("idempotency_key") == key
                for e in DB["time_ledger"]
            )
            if conflict:
                self._r = None  # DO NOTHING -> aucune ligne RETURNING
            else:
                DB["time_ledger"].append(dict(
                    id=str(lid), user_id=uid, bucket="earned", delta_seconds=amount,
                    reason=reason, idempotency_key=key, created_at=created_at,
                ))
                self._r = (str(lid),)

        elif "SELECT COALESCE(earned_seconds_remaining, 0) FROM accounts" in s:
            self._r = (int(DB["accounts"][p[0]].get("earned_seconds_remaining") or 0),)

        elif "UPDATE accounts SET earned_seconds_remaining" in s:
            amount, uid = p
            new_val = (DB["accounts"][uid].get("earned_seconds_remaining") or 0) + amount
            DB["accounts"][uid]["earned_seconds_remaining"] = new_val
            self._r = (new_val,)

        else:
            raise AssertionError("SQL non modélisé (bonus_credit) : " + s)


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
# 1. Crédit simple
# ---------------------------------------------------------------------------
print("=" * 64)
print("1. Crédit simple")
print("=" * 64)

reset_db()
seed_account(UID, earned=0)
r = A.credit_bonus_time(UID, 300, "share_reward", "share:u1:2026-09-09")
check(r["credited"] is True, "1a credited=True au 1er crédit")
check(r["earned_remaining_seconds"] == 300, "1b solde = 300 après crédit")
check(DB["accounts"][UID]["earned_seconds_remaining"] == 300,
      "1c accounts.earned_seconds_remaining réellement mis à jour")
check(len(DB["time_ledger"]) == 1 and DB["time_ledger"][0]["bucket"] == "earned"
      and DB["time_ledger"][0]["delta_seconds"] == 300
      and DB["time_ledger"][0]["reason"] == "share_reward"
      and DB["time_ledger"][0]["idempotency_key"] == "share:u1:2026-09-09",
      "1d une ligne time_ledger, bucket earned, delta positif, reason + clé tracés")

# ---------------------------------------------------------------------------
# 2. Idempotence — même clé rejouée
# ---------------------------------------------------------------------------
print("=" * 64)
print("2. Idempotence (même idempotency_key)")
print("=" * 64)

r2 = A.credit_bonus_time(UID, 300, "share_reward", "share:u1:2026-09-09")
check(r2["credited"] is False, "2a rejeu même clé -> credited=False")
check(r2["earned_remaining_seconds"] == 300, "2b solde INCHANGÉ (toujours 300, pas 600)")
check(DB["accounts"][UID]["earned_seconds_remaining"] == 300,
      "2c aucun double crédit réel en base")
check(len(DB["time_ledger"]) == 1, "2d aucune 2e ligne de ledger écrite")

# Rejoué une 3e fois pour bonne mesure — toujours aucun effet.
r3 = A.credit_bonus_time(UID, 300, "share_reward", "share:u1:2026-09-09")
check(r3["credited"] is False and DB["accounts"][UID]["earned_seconds_remaining"] == 300,
      "2e rejeu une 3e fois -> toujours aucun double crédit")

# ---------------------------------------------------------------------------
# 3. Clé DIFFÉRENTE -> nouveau crédit, cumulatif
# ---------------------------------------------------------------------------
print("=" * 64)
print("3. Clé différente -> crédit cumulatif")
print("=" * 64)

r4 = A.credit_bonus_time(UID, 120, "memory_game", "memory:session-42")
check(r4["credited"] is True, "3a nouvelle clé -> credited=True")
check(r4["earned_remaining_seconds"] == 420, "3b solde cumulé 300 + 120 = 420")
check(len(DB["time_ledger"]) == 2, "3c 2 lignes de ledger au total")

# ---------------------------------------------------------------------------
# 4. Deux comptes distincts — la même clé littérale n'entre PAS en conflit
#    entre deux user_id différents (unicité scoping (user_id, idempotency_key))
# ---------------------------------------------------------------------------
print("=" * 64)
print("4. Scoping par utilisateur")
print("=" * 64)

seed_account(UID2, earned=0)
r5 = A.credit_bonus_time(UID2, 300, "share_reward", "share:u1:2026-09-09")
check(r5["credited"] is True,
      "4a MÊME clé littérale mais AUTRE user_id -> pas un conflit (scopé par user_id)")
check(DB["accounts"][UID2]["earned_seconds_remaining"] == 300,
      "4b crédité normalement pour ce 2e compte")

# ---------------------------------------------------------------------------
# 5. Garde-fous
# ---------------------------------------------------------------------------
print("=" * 64)
print("5. Garde-fous (sécurité / robustesse)")
print("=" * 64)

_raised = False
try:
    A.credit_bonus_time(UID, 0, "x", "k1")
except ValueError:
    _raised = True
check(_raised, "5a seconds=0 -> ValueError (jamais un crédit silencieux de 0)")

_raised = False
try:
    A.credit_bonus_time(UID, -100, "x", "k2")
except ValueError:
    _raised = True
check(_raised, "5b seconds négatif -> ValueError")

_raised = False
try:
    A.credit_bonus_time(UID, 100, "x", "")
except ValueError:
    _raised = True
check(_raised, "5c idempotency_key vide -> ValueError (jamais un crédit non idempotent)")

_raised = False
try:
    A.credit_bonus_time(UID, 100, "x", None)
except (ValueError, TypeError):
    _raised = True
check(_raised, "5d idempotency_key=None -> rejeté")

# Compte inconnu / supprimé -> jamais de crédit, jamais d'exception.
r_unknown = A.credit_bonus_time("00000000-0000-0000-0000-000000000000", 100, "x", "k3")
check(r_unknown["credited"] is False and r_unknown.get("reason") == "unknown_account",
      "5e compte inconnu -> credited=False, reason=unknown_account, aucun crash")

seed_account("33333333-3333-4333-8333-333333333333", deleted=True, earned=50)
r_deleted = A.credit_bonus_time(
    "33333333-3333-4333-8333-333333333333", 100, "x", "k4"
)
check(r_deleted["credited"] is False,
      "5f compte supprimé (deleted_at renseigné) -> aucun crédit")
check(DB["accounts"]["33333333-3333-4333-8333-333333333333"]["earned_seconds_remaining"] == 50,
      "5g solde du compte supprimé inchangé")

print("-" * 64)
total = _STATE["pass"] + _STATE["fail"]
print(f"RESULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko (sur {total})")
sys.exit(1 if _STATE["fail"] else 0)
