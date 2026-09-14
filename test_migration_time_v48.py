"""
test_migration_time_v48.py — GROS CHANTIER ÉCONOMIQUE (Prompt 1/5) :
migration additive v48 (bienvenue 20 min + idempotency_key du bonus).

Même méthode que test_migration_time_v34.py / test_migration_v27.py :
psycopg2 mocké, on prouve la migration au niveau du SOURCE de init_db()
(forme du DDL, additivité stricte, idempotence, non-régression), PUIS une
simulation Python PURE (sans SQL, sans mock DB) de la logique de backfill
pour prouver le comportement 3600->1200 / déjà-consommé / valeur custom.
"""

import sys
import inspect
import re
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


SRC = inspect.getsource(A.init_db)

_m48 = re.search(r"#\s*Migration v48\b", SRC)
assert _m48, "bloc 'Migration v48' introuvable dans init_db()"
_m47 = re.search(r"#\s*Migration v47\b", SRC)
assert _m47, "bloc 'Migration v47' introuvable dans init_db()"
assert _m48.start() > _m47.start(), "v48 doit venir APRES v47"

_tail = SRC[_m48.start():]
_close = _tail.find("conn.close()")
assert _close != -1, "conn.close() final introuvable apres le bloc v48"
V48 = _tail[:_close + len("conn.close()")]


def _nocomment(block):
    return "\n".join(
        line for line in block.splitlines() if not line.lstrip().startswith("#")
    )


V48_NC = _nocomment(V48)
V48_LOW = V48_NC.lower()
V48_FLAT = re.sub(r"[\s\"']+", " ", V48_LOW)

print("-" * 64)
print("A. Ordre & présence dans init_db()")
check("Migration v47" in SRC and SRC.index("Migration v47") < _m48.start(),
      "1 v48 ajoutée APRÈS la migration v47 existante")
check("Migration v34" in SRC and "Migration v42" in SRC and "Migration v44" in SRC,
      "2 les migrations antérieures du moteur temps (v34/v42/v44) sont toujours présentes")

print("-" * 64)
print("B. accounts.first_free_seconds_remaining — SET DEFAULT 1200 (jamais un ADD COLUMN)")
check(
    "first_free_seconds_remaining" in V48_FLAT
    and "alter table accounts add column" not in V48_FLAT,
    "3 v48 n'ajoute AUCUNE colonne sur accounts (déjà posées en v34)",
)
check("alter table accounts alter column first_free_seconds_remaining set default 1200"
      in V48_FLAT,
      "4 ALTER COLUMN accounts.first_free_seconds_remaining SET DEFAULT 1200")
check("set default 3600" not in V48_FLAT,
      "5 v48 ne repose jamais l'ancien DEFAULT 3600")

print("-" * 64)
print("C. Backfill ciblé — jamais un double cadeau, jamais une reprise de solde")
check(
    re.search(
        r"update accounts set first_free_seconds_remaining = 1200 "
        r"where first_consultation_used_at is null "
        r"and first_free_seconds_remaining = 3600",
        V48_FLAT,
    )
    is not None,
    "6 UPDATE ciblé : used_at IS NULL AND ffsr = 3600 (exactement) -> 1200",
)
check("first_consultation_used_at is not null" not in V48_FLAT,
      "7 aucune clause ne touche les comptes DÉJÀ utilisés (par construction "
      "de la condition ci-dessus, jamais une 2e branche qui les toucherait)")
check(V48_FLAT.count("update accounts") == 1,
      "8 UN SEUL UPDATE sur accounts dans tout le bloc v48")
check("delete" not in V48_FLAT and "truncate" not in V48_FLAT and "drop" not in V48_FLAT,
      "9 aucun DELETE / TRUNCATE / DROP dans le bloc v48")

print("-" * 64)
print("D. time_ledger.idempotency_key — colonne + index UNIQUE PARTIEL")
check("alter table time_ledger add column if not exists idempotency_key text"
      in V48_FLAT,
      "10 ADD COLUMN IF NOT EXISTS time_ledger.idempotency_key TEXT")
check(
    re.search(
        r"create unique index if not exists uq_time_ledger_user_idempotency "
        r"on time_ledger \(user_id, idempotency_key\) "
        r"where idempotency_key is not null",
        V48_FLAT,
    )
    is not None,
    "11 index UNIQUE PARTIEL (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL",
)

print("-" * 64)
print("E. Idempotence de la migration elle-même (rejouabilité)")
check("if not exists" in V48_FLAT,
      "12 ADD COLUMN / CREATE INDEX utilisent IF NOT EXISTS (rejouable sans erreur)")
check(V48_FLAT.count("set default") == 1,
      "13 un seul SET DEFAULT (rejouable : reposer le même défaut n'est jamais une erreur)")

print("-" * 64)
print("F. Constante Python alignée (documentation, non lue par le moteur)")
check(A.FIRST_FREE_SECONDS == 1200, "14 FIRST_FREE_SECONDS = 1200 (nouveau standard)")

# ---------------------------------------------------------------------------
# G. Simulation PURE (sans SQL, sans mock DB) du backfill v48 — mêmes 3 cas
# que documentés dans la migration : jamais utilisée + valeur par défaut
# intacte -> 1200 ; déjà utilisée -> inchangée ; valeur custom (admin) ->
# inchangée (ni 3600 ni ambiguë).
# ---------------------------------------------------------------------------
print("-" * 64)
print("G. Simulation pure du backfill (3 cas)")


def _v48_backfill(row):
    """Reproduit EXACTEMENT :
    UPDATE accounts SET ffsr = 1200
    WHERE first_consultation_used_at IS NULL AND ffsr = 3600"""
    r = dict(row)
    if r["first_consultation_used_at"] is None and r["first_free_seconds_remaining"] == 3600:
        r["first_free_seconds_remaining"] = 1200
    return r


# G1 — nouveau/ancien compte JAMAIS utilisé, encore à l'ancien défaut 3600.
_never_used = {"first_consultation_used_at": None, "first_free_seconds_remaining": 3600}
_after = _v48_backfill(_never_used)
check(_after["first_free_seconds_remaining"] == 1200,
      "15 compte jamais utilisé à 3600 -> ramené à 1200")

# G2 — compte ayant DÉJÀ utilisé la gratuite (used_at renseigné), peu importe
# le solde restant (0, ou une valeur partielle d'un débit antérieur).
_used = {"first_consultation_used_at": "2026-01-01T00:00:00Z",
         "first_free_seconds_remaining": 0}
check(_v48_backfill(_used)["first_free_seconds_remaining"] == 0,
      "16 compte déjà utilisé (solde 0) -> JAMAIS retouché")
_used_partial = {"first_consultation_used_at": "2026-01-01T00:00:00Z",
                  "first_free_seconds_remaining": 1800}
check(_v48_backfill(_used_partial)["first_free_seconds_remaining"] == 1800,
      "17 compte déjà utilisé (solde partiel 1800) -> JAMAIS retouché")

# G3 — valeur CUSTOM (ex. ajustement admin), jamais consommée mais PAS à
# exactement 3600 -> stratégie conservatrice : jamais touchée.
_custom = {"first_consultation_used_at": None, "first_free_seconds_remaining": 5000}
check(_v48_backfill(_custom)["first_free_seconds_remaining"] == 5000,
      "18 solde custom (5000, jamais 3600) -> JAMAIS retouché (le plus conservateur)")

# G4 — compte déjà migré une 1re fois (1200) -> rejouer le backfill ne le
# touche plus (n'est plus égal à 3600) : idempotence prouvée.
_already_migrated = {"first_consultation_used_at": None, "first_free_seconds_remaining": 1200}
check(_v48_backfill(_already_migrated)["first_free_seconds_remaining"] == 1200,
      "19 compte déjà migré (1200) -> rejeu sans effet (idempotent)")

# G5 — nouveau compte créé APRÈS v48 (hérite du DEFAULT 1200 directement,
# jamais vu par le backfill car ffsr != 3600 dès l'INSERT).
_brand_new = {"first_consultation_used_at": None, "first_free_seconds_remaining": 1200}
check(_v48_backfill(_brand_new)["first_free_seconds_remaining"] == 1200,
      "20 nouveau compte (DEFAULT 1200 dès l'INSERT) -> backfill sans effet")

print("-" * 64)
total = _STATE["pass"] + _STATE["fail"]
print(f"RESULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko (sur {total})")
sys.exit(1 if _STATE["fail"] else 0)
