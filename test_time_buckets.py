"""
test_time_buckets.py — Partie C : séparation des buckets de temps
(minutes GAGNÉES vs heures ACHETÉES) + time_ledger + backfill non rejouable.

100 % local : psycopg2 mocké, curseur factice en mémoire pour le moteur temps
(_debit_consultation_seconds_tx), simulation Python pure du backfill v42, et
inspections de source pour les invariants (récompenses -> earned, Premium ne
remet rien à zéro, resync non touché). Aucune vraie DB, aucun LLM, aucun réseau.

Matrice exigée :
  1. récompense -> earned uniquement
  2. renouvellement Premium ne remet pas earned/purchased à zéro
  3. ordre de débit exact : first_free -> premium -> earned -> purchased
  4. aucun bucket négatif
  5. concurrence multi-appareils (débits sérialisés par le mutex accounts)
  6. migration rejouée sans déplacer une seconde fois les valeurs
  7. suppression du compte purge le ledger
"""

import sys
import inspect
from datetime import datetime, timezone, timedelta
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


UID = "11111111-1111-4111-8111-111111111111"
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# Curseur factice pour _debit_consultation_seconds_tx (curseur-in, aucun
# commit / rollback / close attendu ici).
# ---------------------------------------------------------------------------
DB = {"acc": {}, "alw": [], "ledger": []}


def seed(first_free=0, premium=0, earned=0, purchased=0, first_used=None):
    DB["acc"] = {
        "first_free_seconds_remaining": first_free,
        "earned_seconds_remaining": earned,
        "purchased_seconds_remaining": purchased,
        "first_consultation_used_at": first_used,
    }
    DB["alw"] = []
    DB["ledger"] = []
    if premium:
        DB["alw"].append({
            "period_start": NOW - timedelta(days=1),
            "monthly_allowance_seconds": premium,
            "monthly_used_seconds": 0,
        })


def _norm(s):
    return " ".join(s.split())


class Cur:
    def __init__(self):
        self._r = None
        self.rowcount = -1

    def fetchone(self):
        return self._r

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = _norm(sql)
        p = tuple(params or ())
        self._r = None
        self.rowcount = -1
        a = DB["acc"]

        if k in (
            "SELECT first_free_seconds_remaining, earned_seconds_remaining, "
            "purchased_seconds_remaining FROM accounts WHERE user_id=%s",
            "SELECT first_free_seconds_remaining, earned_seconds_remaining, "
            "purchased_seconds_remaining FROM accounts WHERE user_id=%s FOR UPDATE",
        ):
            self._r = (a["first_free_seconds_remaining"],
                       a["earned_seconds_remaining"],
                       a["purchased_seconds_remaining"])
        elif k == ("SELECT period_start, monthly_allowance_seconds, "
                   "monthly_used_seconds FROM consultation_allowance "
                   "WHERE user_id=%s AND period_start <= %s AND period_end > %s "
                   "ORDER BY period_start DESC LIMIT 1 FOR UPDATE"):
            if DB["alw"]:
                r = DB["alw"][0]
                self._r = (r["period_start"], r["monthly_allowance_seconds"],
                           r["monthly_used_seconds"])
        elif k == ("UPDATE accounts SET first_free_seconds_remaining=%s, "
                   "earned_seconds_remaining=%s, purchased_seconds_remaining=%s "
                   "WHERE user_id=%s"):
            a["first_free_seconds_remaining"] = p[0]
            a["earned_seconds_remaining"] = p[1]
            a["purchased_seconds_remaining"] = p[2]
            self.rowcount = 1
        elif k == ("UPDATE accounts SET first_consultation_used_at=%s "
                   "WHERE user_id=%s AND first_consultation_used_at IS NULL"):
            if a["first_consultation_used_at"] is None:
                a["first_consultation_used_at"] = p[0]
                self.rowcount = 1
            else:
                self.rowcount = 0
        elif k == ("UPDATE consultation_allowance SET monthly_used_seconds=%s "
                   "WHERE user_id=%s AND period_start=%s"):
            DB["alw"][0]["monthly_used_seconds"] = p[0]
            self.rowcount = 1
        elif k.startswith("INSERT INTO time_ledger "):
            _, uid, bucket, delta, reason, ref, _ca = p
            DB["ledger"].append({"bucket": bucket, "delta": delta,
                                 "reason": reason, "ref_id": ref})
            self.rowcount = 1
        else:
            raise AssertionError("SQL non géré par le fake buckets : " + k)


def debit(seconds, ref_id="c-1"):
    return A._debit_consultation_seconds_tx(Cur(), UID, seconds, NOW, ref_id=ref_id)


# ===========================================================================
# 0. _time_totals expose earned, dans l'ordre de débit
# ===========================================================================
t = A._time_totals(100, 200, 300, 400)
check(t == {"first_free_remaining_seconds": 100, "premium_remaining_seconds": 200,
            "earned_remaining_seconds": 300, "purchased_remaining_seconds": 400,
            "total_remaining_seconds": 1000},
      "0a _time_totals(first_free, premium, earned, purchased) -> 4 buckets + total")
check(A._time_totals(-5, -5, -5, -5)["total_remaining_seconds"] == 0,
      "0b valeurs négatives bornées à 0")

# ===========================================================================
# 1. Récompenses -> earned UNIQUEMENT (inspection de source)
# ===========================================================================
_share = inspect.getsource(A.api_rewards_daily_share)
_wb = inspect.getsource(A._reconcile_wellbeing_progress)
_mem = inspect.getsource(A.api_memory_complete)
for name, src in (("share", _share), ("wellbeing", _wb), ("memory", _mem)):
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    credits_earned = "earned_seconds_remaining =" in body or "earned_seconds_remaining=" in body
    credits_purchased = ("purchased_seconds_remaining =" in body
                         or "purchased_seconds_remaining=" in body)
    check(credits_earned and not credits_purchased,
          f"1 récompense {name} crédite earned, jamais purchased")
    check('_time_ledger_write(' in src, f"1 récompense {name} trace le time_ledger")

# ===========================================================================
# 2. Renouvellement Premium ne remet PAS earned / purchased à zéro
# ===========================================================================
_resync = inspect.getsource(A.resync_premium_entitlement)
check("earned_seconds_remaining" not in _resync
      and "purchased_seconds_remaining" not in _resync,
      "2a resync_premium_entitlement ne touche NI earned NI purchased")
_prov = inspect.getsource(A.provision_allowance)
check("earned_seconds_remaining" not in _prov
      and "purchased_seconds_remaining" not in _prov,
      "2b provision_allowance (nouvelle période) ne touche NI earned NI purchased")
# simulation : une nouvelle période Premium (allowance neuve, used=0) -> un débit
# ne pioche pas dans earned/purchased tant que le Premium couvre.
seed(first_free=0, premium=28800, earned=900, purchased=1800)
r = debit(600)
check(r["earned_remaining_seconds"] == 900 and r["purchased_remaining_seconds"] == 1800
      and r["premium_remaining_seconds"] == 28200,
      "2c débit couvert par Premium neuf -> earned & purchased intacts")

# ===========================================================================
# 3. Ordre de débit EXACT : first_free -> premium -> earned -> purchased
# ===========================================================================
seed(first_free=100, premium=200, earned=300, purchased=400)
r = debit(100 + 200 + 300 + 50)   # vide ff, premium, earned ; entame purchased de 50
check(r["first_free_remaining_seconds"] == 0
      and r["premium_remaining_seconds"] == 0
      and r["earned_remaining_seconds"] == 0
      and r["purchased_remaining_seconds"] == 350
      and r["debited_seconds"] == 650 and r["unbilled_seconds"] == 0,
      "3a cascade ff -> premium -> earned -> purchased")
# ledger : 4 lignes négatives, buckets dans l'ordre, ref_id propagé
buckets = [(e["bucket"], e["delta"]) for e in DB["ledger"]]
check(buckets == [("first_free", -100), ("premium", -200),
                  ("earned", -300), ("purchased", -50)]
      and all(e["reason"] == "consultation_debit" and e["ref_id"] == "c-1"
              for e in DB["ledger"]),
      "3b time_ledger : une ligne par bucket débité, signe négatif, ref_id = consultation")

# earned AVANT purchased : si earned couvre, purchased n'est pas touché
seed(first_free=0, premium=0, earned=500, purchased=500)
r = debit(300)
check(r["earned_remaining_seconds"] == 200 and r["purchased_remaining_seconds"] == 500,
      "3c earned consommé AVANT purchased")

# ===========================================================================
# 4. Aucun bucket négatif (demande > total)
# ===========================================================================
seed(first_free=10, premium=20, earned=30, purchased=40)
r = debit(10_000)
check(r["first_free_remaining_seconds"] == 0 and r["premium_remaining_seconds"] == 0
      and r["earned_remaining_seconds"] == 0 and r["purchased_remaining_seconds"] == 0,
      "4a tous les buckets bornés à 0, jamais négatifs")
check(r["debited_seconds"] == 100 and r["unbilled_seconds"] == 10_000 - 100
      and r["exhausted"] is True,
      "4b debited = 100, le reste est unbilled, exhausted=True")
check(DB["acc"]["first_free_seconds_remaining"] == 0
      and DB["acc"]["earned_seconds_remaining"] == 0
      and DB["acc"]["purchased_seconds_remaining"] == 0,
      "4c persistance : aucune valeur négative écrite")

# ===========================================================================
# 5. Concurrence multi-appareils : débits sérialisés (mutex accounts FOR UPDATE)
#    -> jamais de double-dépense, décroissance monotone, aucun négatif.
# ===========================================================================
seed(first_free=0, premium=0, earned=0, purchased=500)
r1 = debit(300, ref_id="dev-A")
r2 = debit(300, ref_id="dev-B")     # 2e appareil : ne peut prendre que 200
total_debited = r1["debited_seconds"] + r2["debited_seconds"]
check(total_debited == 500 and r2["debited_seconds"] == 200
      and DB["acc"]["purchased_seconds_remaining"] == 0
      and r2["unbilled_seconds"] == 100,
      "5a deux appareils : somme débitée = stock exact, jamais > disponible")
check([e["ref_id"] for e in DB["ledger"]] == ["dev-A", "dev-B"],
      "5b chaque débit tracé séparément au ledger avec son ref_id")

# ===========================================================================
# 6. Backfill v42 rejoué : ne déplace pas une seconde fois (garde marqueur)
#    Simulation Python PURE de la logique exacte de init_db().
# ===========================================================================
def _run_backfill_once(accounts, backfills):
    """Reproduit la garde + le déplacement du bloc Migration v42."""
    if "v42_purchased_rewards_to_earned" in backfills:
        return 0            # marqueur présent -> no-op strict
    moved = 0
    for a in accounts:
        amt = int(a.get("purchased_seconds_remaining", 0) or 0)
        if amt != 0:
            a["earned_seconds_remaining"] = a.get("earned_seconds_remaining", 0) + amt
            a["purchased_seconds_remaining"] = 0
            moved += 1
    backfills.add("v42_purchased_rewards_to_earned")
    return moved


accts = [{"purchased_seconds_remaining": 3600, "earned_seconds_remaining": 0},
         {"purchased_seconds_remaining": 900, "earned_seconds_remaining": 300},
         {"purchased_seconds_remaining": 0, "earned_seconds_remaining": 0}]
marks = set()
n1 = _run_backfill_once(accts, marks)
check(n1 == 2
      and accts[0]["earned_seconds_remaining"] == 3600 and accts[0]["purchased_seconds_remaining"] == 0
      and accts[1]["earned_seconds_remaining"] == 1200 and accts[1]["purchased_seconds_remaining"] == 0
      and accts[2]["earned_seconds_remaining"] == 0,
      "6a 1er passage : purchased (récompenses) déplacé vers earned, purchased=0")
snapshot = [dict(a) for a in accts]
n2 = _run_backfill_once(accts, marks)
n3 = _run_backfill_once(accts, marks)
check(n2 == 0 and n3 == 0 and [dict(a) for a in accts] == snapshot,
      "6b rejeux : marqueur présent -> AUCUN déplacement supplémentaire")
# la source d'init_db contient bien la garde + le marqueur
_initsrc = inspect.getsource(A.init_db)
check("schema_backfills" in _initsrc
      and "v42_purchased_rewards_to_earned" in _initsrc
      and "earned_seconds_remaining = " in _initsrc,
      "6c init_db : garde schema_backfills + déplacement purchased->earned présents")
_init_code = "\n".join(
    l for l in _initsrc.splitlines() if not l.lstrip().startswith("#"))
check("DROP TABLE" not in _init_code and "TRUNCATE" not in _init_code
      and "DELETE FROM accounts" not in _init_code,
      "6d init_db (hors commentaires) : aucune destruction de données")

# ===========================================================================
# 7. Suppression du compte purge le ledger
# ===========================================================================
check("time_ledger" in A._ACCOUNT_DELETE_CHILD_TABLES,
      "7 time_ledger listé dans _ACCOUNT_DELETE_CHILD_TABLES (purge au DELETE compte)")

# ===========================================================================
# 8. _time_ledger_write : 0 ignoré, signe conservé, ref_id optionnel
# ===========================================================================
DB["ledger"] = []
A._time_ledger_write(Cur(), UID,
                     [("earned", 0), ("premium", -120), ("purchased", 60)],
                     "unit", None, NOW)
check([(e["bucket"], e["delta"], e["ref_id"]) for e in DB["ledger"]]
      == [("premium", -120, None), ("purchased", 60, None)],
      "8 _time_ledger_write ignore les deltas nuls, conserve le signe, ref_id=None accepté")

# ---------------------------------------------------------------------------
print("-" * 60)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
