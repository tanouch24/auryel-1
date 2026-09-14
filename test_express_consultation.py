"""
test_express_consultation.py — GROS CHANTIER AURYEL (Prompt 3/5) :
CONSULTATION EXPRESS (dépenser des Étoiles contre du temps de consultation).

Teste directement `purchase_express_consultation` / `_purchase_express_
consultation_tx` (composition de `_debit_stars_tx` et `_credit_bonus_time_tx`,
toutes deux déjà éprouvées par leurs propres suites — Prompt 1/5 et 2/5) puis
la route HTTP `POST /api/app/rewards/express-consultation`.

100 % local : psycopg2 mocké, FakeConn/FakeCursor en mémoire. Aucune vraie
DB, aucun réseau, aucun LLM.
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


NOW = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)
A._utcnow = lambda: NOW

UID = "11111111-1111-4111-8111-111111111111"
UID2 = "22222222-2222-4222-8222-222222222222"

DB = {
    "accounts": {},
    "reward_wallet": {},
    "express_products": {},
    "express_consultations": [],
    "reward_transactions": [],
    "time_ledger": [],
}


def _default_wallet():
    return {"stars_balance": 0}


def _default_account():
    return {"first_free_seconds_remaining": 0, "earned_seconds_remaining": 0,
            "purchased_seconds_remaining": 0, "deleted_at": None}


def reset_db():
    DB["accounts"].clear()
    DB["reward_wallet"].clear()
    DB["express_products"].clear()
    DB["express_consultations"].clear()
    DB["reward_transactions"].clear()
    DB["time_ledger"].clear()
    DB["express_products"]["express_consultation_10min"] = {
        "stars_cost": 500, "seconds_granted": 600, "enabled": True,
    }


def seed_account(uid, stars=0, deleted=False):
    DB["accounts"][uid] = dict(_default_account(), deleted_at=(NOW if deleted else None))
    DB["reward_wallet"][uid] = {"stars_balance": stars}


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
            self._r = (p[0],) if row and row["deleted_at"] is None else None

        elif "stars_spent, seconds_granted, status FROM express_consultations" in s:
            uid, idem = p
            match = next(
                (e for e in DB["express_consultations"]
                 if e["user_id"] == uid and e["idempotency_key"] == idem), None
            )
            self._r = (match["stars_spent"], match["seconds_granted"], match["status"]) \
                if match else None

        elif "INSERT INTO express_consultations" in s:
            (eid, uid, stars_spent, seconds_granted, idem, created_at) = p
            DB["express_consultations"].append(dict(
                id=eid, user_id=uid, stars_spent=stars_spent,
                seconds_granted=seconds_granted, status="completed",
                idempotency_key=idem, created_at=created_at,
            ))

        elif "SELECT stars_cost, seconds_granted, enabled FROM express_products" in s:
            prod = DB["express_products"].get(p[0])
            self._r = (prod["stars_cost"], prod["seconds_granted"], prod["enabled"]) \
                if prod else None

        elif "INSERT INTO reward_wallet (user_id, stars_balance, updated_at)" in s:
            uid = p[0]
            DB["reward_wallet"].setdefault(uid, _default_wallet())

        elif "SELECT stars_balance FROM reward_wallet WHERE user_id=%s FOR UPDATE" in s:
            uid = p[0]
            w = DB["reward_wallet"].setdefault(uid, _default_wallet())
            self._r = (w["stars_balance"],)

        elif "SELECT stars_balance FROM reward_wallet WHERE user_id=%s" in s:
            uid = p[0]
            w = DB["reward_wallet"].get(uid)
            self._r = (w["stars_balance"] if w else 0,)

        elif "SELECT balance_after FROM reward_transactions" in s:
            uid, idem = p
            match = next(
                (t for t in DB["reward_transactions"]
                 if t["user_id"] == uid and t.get("idempotency_key") == idem), None
            )
            self._r = (match["balance_after"],) if match else None

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
                    reason=reason, idempotency_key=idem, created_at=created_at,
                ))
                self._r = (tid,)

        elif "UPDATE reward_wallet SET stars_balance=%s, updated_at=%s" in s:
            new_balance, now, uid = p
            DB["reward_wallet"].setdefault(uid, _default_wallet())
            DB["reward_wallet"][uid]["stars_balance"] = new_balance

        elif "INSERT INTO time_ledger" in s and "idempotency_key" in s:
            (lid, uid, amount, reason, key, created_at) = p
            conflict = key is not None and any(
                e["user_id"] == uid and e.get("idempotency_key") == key
                for e in DB["time_ledger"]
            )
            if conflict:
                self._r = None
            else:
                DB["time_ledger"].append(dict(
                    id=lid, user_id=uid, delta_seconds=amount, reason=reason,
                    idempotency_key=key, created_at=created_at,
                ))
                self._r = (lid,)

        elif "SELECT COALESCE(earned_seconds_remaining, 0) FROM accounts" in s:
            uid = p[0]
            self._r = (int(DB["accounts"][uid].get("earned_seconds_remaining") or 0),)

        elif "UPDATE accounts SET earned_seconds_remaining" in s:
            amount, uid = p
            new_val = (DB["accounts"][uid].get("earned_seconds_remaining") or 0) + amount
            DB["accounts"][uid]["earned_seconds_remaining"] = new_val
            self._r = (new_val,)

        elif ("SELECT first_free_seconds_remaining, earned_seconds_remaining, "
              "purchased_seconds_remaining") in s:
            uid = p[0]
            acc = DB["accounts"][uid]
            self._r = (acc["first_free_seconds_remaining"], acc["earned_seconds_remaining"],
                       acc["purchased_seconds_remaining"])

        elif "monthly_allowance_seconds, monthly_used_seconds" in s:
            self._r = None  # aucune période Premium active dans ces tests

        else:
            raise AssertionError("SQL non modélisé (express) : " + s)


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
# 1. Achat simple — 500 -> 0 Étoiles, +600 s bonus
# ---------------------------------------------------------------------------
print("=" * 64)
print("1. Achat simple")
print("=" * 64)

reset_db()
seed_account(UID, stars=500)
r = A.purchase_express_consultation(UID, "express_consultation_10min", "expr:u1:1")
check(r["success"] is True, "1a success=True")
check(r["stars_spent"] == 500, "1b stars_spent = 500 (résolu depuis express_products)")
check(r["stars_balance"] == 0, "1c stars_balance = 0 après achat (500 -> 0)")
check(r["seconds_granted"] == 600, "1d seconds_granted = 600")
check(r["balances"]["earned_remaining_seconds"] == 600,
      "1e bucket bonus (earned) crédité de 600 s, AUCUN 5e bucket créé")
check(DB["reward_wallet"][UID]["stars_balance"] == 0, "1f wallet réellement débité")
check(DB["accounts"][UID]["earned_seconds_remaining"] == 600, "1g accounts réellement crédité")
check(len(DB["express_consultations"]) == 1
      and DB["express_consultations"][0]["stars_spent"] == 500
      and DB["express_consultations"][0]["seconds_granted"] == 600
      and DB["express_consultations"][0]["status"] == "completed",
      "1h une ligne express_consultations, trace correcte")

# ---------------------------------------------------------------------------
# 2. wallet 1000 -> 500 après achat
# ---------------------------------------------------------------------------
print("=" * 64)
print("2. Wallet 1000 -> 500")
print("=" * 64)

reset_db()
seed_account(UID, stars=1000)
r = A.purchase_express_consultation(UID, "express_consultation_10min", "expr:u1:2")
check(r["success"] is True and r["stars_balance"] == 500,
      "2a 1000 - 500 = 500 restant")

# ---------------------------------------------------------------------------
# 3. Solde insuffisant (499) -> refus, AUCUNE dépense, AUCUN crédit
# ---------------------------------------------------------------------------
print("=" * 64)
print("3. Solde insuffisant")
print("=" * 64)

reset_db()
seed_account(UID, stars=499)
r = A.purchase_express_consultation(UID, "express_consultation_10min", "expr:u1:3")
check(r["success"] is False and r["reason"] == "insufficient_balance",
      "3a 499 < 500 -> refus insufficient_balance")
check(r["stars_balance"] == 499, "3b solde INCHANGÉ (jamais débité)")
check(DB["accounts"][UID]["earned_seconds_remaining"] == 0,
      "3c AUCUN crédit de temps (aucune dépense -> aucun crédit)")
check(len(DB["express_consultations"]) == 0,
      "3d AUCUNE trace express_consultations pour un achat refusé")

# ---------------------------------------------------------------------------
# 4. Exactement 500 -> succès (borne inclusive)
# ---------------------------------------------------------------------------
print("=" * 64)
print("4. Exactement 500 Étoiles")
print("=" * 64)

reset_db()
seed_account(UID, stars=500)
r = A.purchase_express_consultation(UID, "express_consultation_10min", "expr:u1:4")
check(r["success"] is True and r["stars_balance"] == 0,
      "4a exactement 500 -> succès, solde à 0")

# ---------------------------------------------------------------------------
# 5. Produit désactivé -> refus
# ---------------------------------------------------------------------------
print("=" * 64)
print("5. Produit désactivé")
print("=" * 64)

reset_db()
DB["express_products"]["express_consultation_10min"]["enabled"] = False
seed_account(UID, stars=500)
r = A.purchase_express_consultation(UID, "express_consultation_10min", "expr:u1:5")
check(r["success"] is False and r["reason"] == "product_disabled",
      "5a produit désactivé -> refus, solde inchangé")
check(r["stars_balance"] == 500, "5b solde intact")

# ---------------------------------------------------------------------------
# 6. Produit inconnu -> refus
# ---------------------------------------------------------------------------
print("=" * 64)
print("6. Produit inconnu")
print("=" * 64)

reset_db()
seed_account(UID, stars=500)
r = A.purchase_express_consultation(UID, "does_not_exist", "expr:u1:6")
check(r["success"] is False and r["reason"] == "unknown_product",
      "6a clé de produit inconnue -> refus, aucun crash")

# ---------------------------------------------------------------------------
# 7. Idempotence — même idempotency_key rejouée (double tap / retry réseau /
#    réponse perdue puis retry) -> UNE seule dépense, UN seul crédit
# ---------------------------------------------------------------------------
print("=" * 64)
print("7. Idempotence — rejeu de la même clé")
print("=" * 64)

reset_db()
seed_account(UID, stars=1000)
r1 = A.purchase_express_consultation(UID, "express_consultation_10min", "expr:u1:7")
check(r1["success"] is True and r1["stars_balance"] == 500, "7a 1er achat -> 500 restant")
r2 = A.purchase_express_consultation(UID, "express_consultation_10min", "expr:u1:7")
check(r2["success"] is True and r2["stars_balance"] == 500,
      "7b rejeu de la MÊME clé -> renvoie le résultat déjà acquis (toujours "
      "500, PAS 0 — aucun 2e débit)")
r3 = A.purchase_express_consultation(UID, "express_consultation_10min", "expr:u1:7")
check(r3["stars_balance"] == 500, "7c 3e rejeu -> toujours 500")
check(len(DB["express_consultations"]) == 1, "7d UNE SEULE ligne express_consultations")
check(len(DB["reward_transactions"]) == 1, "7e UNE SEULE transaction Étoiles (spend)")
check(len(DB["time_ledger"]) == 1, "7f UNE SEULE ligne time_ledger (crédit bonus)")
check(DB["accounts"][UID]["earned_seconds_remaining"] == 600,
      "7g UN SEUL crédit de 600 s (pas 1200 après 3 tentatives)")

# ---------------------------------------------------------------------------
# 8. Clé DIFFÉRENTE -> nouvel achat, cumulatif
# ---------------------------------------------------------------------------
print("=" * 64)
print("8. Clé différente -> nouvel achat")
print("=" * 64)

r4 = A.purchase_express_consultation(UID, "express_consultation_10min", "expr:u1:8")
check(r4["success"] is True and r4["stars_balance"] == 0,
      "8a nouvelle clé -> nouveau débit (500 - 500 = 0)")
check(DB["accounts"][UID]["earned_seconds_remaining"] == 1200,
      "8b 2 achats distincts -> 1200 s cumulées")
check(len(DB["express_consultations"]) == 2, "8c 2 lignes express_consultations")

# ---------------------------------------------------------------------------
# 9. Scoping par utilisateur — même clé littérale, users différents
# ---------------------------------------------------------------------------
print("=" * 64)
print("9. Scoping par utilisateur")
print("=" * 64)

reset_db()
seed_account(UID, stars=500)
seed_account(UID2, stars=500)
A.purchase_express_consultation(UID, "express_consultation_10min", "expr:shared")
r = A.purchase_express_consultation(UID2, "express_consultation_10min", "expr:shared")
check(r["success"] is True and r["stars_balance"] == 0,
      "9a MÊME clé littérale mais AUTRE user_id -> pas un conflit")
check(DB["accounts"][UID]["earned_seconds_remaining"] == 600
      and DB["accounts"][UID2]["earned_seconds_remaining"] == 600,
      "9b chaque compte crédité indépendamment")

# ---------------------------------------------------------------------------
# 10. Compte inconnu / supprimé
# ---------------------------------------------------------------------------
print("=" * 64)
print("10. Comptes invalides")
print("=" * 64)

r = A.purchase_express_consultation("00000000-0000-0000-0000-000000000000",
                                     "express_consultation_10min", "expr:x")
check(r["success"] is False and r["reason"] == "unknown_account",
      "10a compte inconnu -> refus, aucun crash")

reset_db()
seed_account(UID, stars=500, deleted=True)
r = A.purchase_express_consultation(UID, "express_consultation_10min", "expr:y")
check(r["success"] is False and r["reason"] == "unknown_account",
      "10b compte supprimé -> refus")

# ---------------------------------------------------------------------------
# 11. Garde-fous idempotency_key
# ---------------------------------------------------------------------------
print("=" * 64)
print("11. Garde-fous")
print("=" * 64)

reset_db()
seed_account(UID, stars=500)
_raised = False
try:
    A.purchase_express_consultation(UID, "express_consultation_10min", "")
except ValueError:
    _raised = True
check(_raised, "11a idempotency_key vide -> ValueError")

_raised = False
try:
    A.purchase_express_consultation(UID, "express_consultation_10min", None)
except ValueError:
    _raised = True
check(_raised, "11b idempotency_key=None -> ValueError")

# ---------------------------------------------------------------------------
# 12. Signature — aucun paramètre de montant/durée
# ---------------------------------------------------------------------------
print("=" * 64)
print("12. Sécurité — signature sans montant/durée")
print("=" * 64)

import inspect as _inspect
_sig = _inspect.signature(A.purchase_express_consultation)
_params = set(_sig.parameters)
check(not ({"stars_cost", "seconds_granted", "amount", "duration"} & _params),
      f"12a purchase_express_consultation() n'accepte NI coût NI durée "
      f"(paramètres réels : {sorted(_params)})")

# ---------------------------------------------------------------------------
# 13. CORRECTIF ÉCONOMIE v2 (Prompt technique 1) — nouveaux paliers
#     Étoiles -> temps : 400=10min, 500=15min, 1000=30min, 1500=45min,
#     2000=60min. Choix DISCRETS (produits), chacun testé au centime près.
# ---------------------------------------------------------------------------
print("=" * 64)
print("13. Nouveaux paliers Étoiles -> temps (400/500/1000/1500/2000)")
print("=" * 64)


def seed_full_catalog():
    """Catalogue COMPLET de la nouvelle économie (5 produits discrets)."""
    DB["express_products"]["express_consultation_10min"] = {
        "stars_cost": 400, "seconds_granted": 600, "enabled": True,
    }
    DB["express_products"]["express_consultation_15min"] = {
        "stars_cost": 500, "seconds_granted": 900, "enabled": True,
    }
    DB["express_products"]["express_consultation_30min"] = {
        "stars_cost": 1000, "seconds_granted": 1800, "enabled": True,
    }
    DB["express_products"]["express_consultation_45min"] = {
        "stars_cost": 1500, "seconds_granted": 2700, "enabled": True,
    }
    DB["express_products"]["express_consultation_60min"] = {
        "stars_cost": 2000, "seconds_granted": 3600, "enabled": True,
    }


# 399 ⭐ : le palier 10 min (400) reste hors de portée.
reset_db()
seed_full_catalog()
seed_account(UID, stars=399)
r = A.purchase_express_consultation(UID, "express_consultation_10min", "e13:a")
check(r["success"] is False and r["reason"] == "insufficient_balance",
      "13a 399 ⭐ -> PAS de 10 min (insufficient_balance)")
check(DB["reward_wallet"][UID]["stars_balance"] == 399,
      "13b solde intact (399, aucun débit partiel)")

# 400 ⭐ : exactement le prix des 10 min -> solde 0 ensuite.
reset_db()
seed_full_catalog()
seed_account(UID, stars=400)
r = A.purchase_express_consultation(UID, "express_consultation_10min", "e13:b")
check(r["success"] is True and r["seconds_granted"] == 600
      and r["stars_spent"] == 400,
      "13c 400 ⭐ -> 10 min (600 s), coût exact 400 ⭐")
check(r["stars_balance"] == 0, "13d reliquat 0 ⭐ après un achat pile au prix")

# 430 ⭐ : achat possible de 10 min, reliquat 30 ⭐ (exemple du rapport).
reset_db()
seed_full_catalog()
seed_account(UID, stars=430)
r = A.purchase_express_consultation(UID, "express_consultation_10min", "e13:c")
check(r["success"] is True and r["seconds_granted"] == 600,
      "13e 430 ⭐ -> achat 10 min possible")
check(r["stars_balance"] == 30, "13f reliquat 30 ⭐ (430 - 400)")

# 499 ⭐ : SEULEMENT le palier 10 min (400) est accessible, pas 15 min (500).
reset_db()
seed_full_catalog()
seed_account(UID, stars=499)
r10 = A.purchase_express_consultation(UID, "express_consultation_10min", "e13:d1")
check(r10["success"] is True, "13g 499 ⭐ -> option 10 min (400) accessible")
reset_db()
seed_full_catalog()
seed_account(UID, stars=499)
r15 = A.purchase_express_consultation(UID, "express_consultation_15min", "e13:d2")
check(r15["success"] is False and r15["reason"] == "insufficient_balance",
      "13h 499 ⭐ -> option 15 min (500) INACCESSIBLE (il manque 1 ⭐)")

# 500 ⭐ : exactement le prix des 15 min.
reset_db()
seed_full_catalog()
seed_account(UID, stars=500)
r = A.purchase_express_consultation(UID, "express_consultation_15min", "e13:e")
check(r["success"] is True and r["seconds_granted"] == 900
      and r["stars_spent"] == 500,
      "13i 500 ⭐ -> 15 min (900 s), coût exact 500 ⭐")
check(r["stars_balance"] == 0, "13j reliquat 0 ⭐")

# 520 ⭐ : achat possible de 15 min, reliquat 20 ⭐ (exemple du rapport).
reset_db()
seed_full_catalog()
seed_account(UID, stars=520)
r = A.purchase_express_consultation(UID, "express_consultation_15min", "e13:f")
check(r["success"] is True and r["stars_balance"] == 20,
      "13k 520 ⭐ -> 15 min, reliquat 20 ⭐ (520 - 500)")

# 1000 ⭐ : exactement le prix des 30 min.
reset_db()
seed_full_catalog()
seed_account(UID, stars=1000)
r = A.purchase_express_consultation(UID, "express_consultation_30min", "e13:g")
check(r["success"] is True and r["seconds_granted"] == 1800
      and r["stars_spent"] == 1000,
      "13l 1000 ⭐ -> 30 min (1800 s), coût exact 1000 ⭐")
check(r["stars_balance"] == 0, "13m reliquat 0 ⭐")

# 1230 ⭐ : achat possible de 30 min, reliquat 230 ⭐ (exemple du rapport) —
# le palier 45 min (1500) reste hors de portée.
reset_db()
seed_full_catalog()
seed_account(UID, stars=1230)
r30 = A.purchase_express_consultation(UID, "express_consultation_30min", "e13:h1")
check(r30["success"] is True and r30["stars_balance"] == 230,
      "13n 1230 ⭐ -> 30 min possible, reliquat 230 ⭐ (1230 - 1000)")
reset_db()
seed_full_catalog()
seed_account(UID, stars=1230)
r45 = A.purchase_express_consultation(UID, "express_consultation_45min", "e13:h2")
check(r45["success"] is False and r45["reason"] == "insufficient_balance",
      "13o 1230 ⭐ -> 45 min (1500) INACCESSIBLE")

# 1500 / 2000 ⭐ : paliers 45 min / 60 min, coût exact.
reset_db()
seed_full_catalog()
seed_account(UID, stars=1500)
r = A.purchase_express_consultation(UID, "express_consultation_45min", "e13:i")
check(r["success"] is True and r["seconds_granted"] == 2700
      and r["stars_balance"] == 0,
      "13p 1500 ⭐ -> 45 min (2700 s), reliquat 0 ⭐")

reset_db()
seed_full_catalog()
seed_account(UID, stars=2000)
r = A.purchase_express_consultation(UID, "express_consultation_60min", "e13:j")
check(r["success"] is True and r["seconds_granted"] == 3600
      and r["stars_balance"] == 0,
      "13q 2000 ⭐ -> 60 min (3600 s), reliquat 0 ⭐")

# Double achat idempotent sur un nouveau palier (rejeu de la MÊME clé).
reset_db()
seed_full_catalog()
seed_account(UID, stars=1000)
first = A.purchase_express_consultation(UID, "express_consultation_30min", "e13:k")
second = A.purchase_express_consultation(UID, "express_consultation_30min", "e13:k")
check(first["success"] is True and second["success"] is True
      and second["stars_spent"] == 1000 and second["stars_balance"] == 0,
      "13r rejeu de la MÊME clé -> renvoie le résultat ORIGINAL (succès), "
      "jamais un 2e débit")
check(len(DB["express_consultations"]) == 1,
      "13s UNE seule ligne express_consultations malgré le rejeu")

# Isolation stricte entre comptes sur le nouveau catalogue.
reset_db()
seed_full_catalog()
seed_account(UID, stars=1000)
seed_account(UID2, stars=50)
A.purchase_express_consultation(UID, "express_consultation_30min", "e13:l")
r_b = A.purchase_express_consultation(UID2, "express_consultation_30min", "e13:l")
check(r_b["success"] is False and r_b["reason"] == "insufficient_balance",
      "13t compte B (50 ⭐) ne bénéficie JAMAIS de l'achat du compte A "
      "(même idempotency_key littérale, scopée par user_id)")
check(DB["reward_wallet"][UID]["stars_balance"] == 0
      and DB["reward_wallet"][UID2]["stars_balance"] == 50,
      "13u soldes isolés par compte (A=0 débité, B=50 intact)")

print("-" * 64)
total = _STATE["pass"] + _STATE["fail"]
print(f"RESULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko (sur {total})")
sys.exit(1 if _STATE["fail"] else 0)
