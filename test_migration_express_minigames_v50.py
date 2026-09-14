"""
test_migration_express_minigames_v50.py — GROS CHANTIER AURYEL (Prompt 3/5) :
migration additive v50 (express_products / express_consultations /
mini_game_sessions / memory_games.stars_awarded / activation de
mini_game_completed).

Même méthode que test_migration_reward_stars_v49.py : psycopg2 mocké, on
prouve la migration au niveau du SOURCE de init_db().
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

_m50 = re.search(r"#\s*Migration v50\b", SRC)
assert _m50, "bloc 'Migration v50' introuvable dans init_db()"
_m49 = re.search(r"#\s*Migration v49\b", SRC)
assert _m49, "bloc 'Migration v49' introuvable dans init_db()"
assert _m50.start() > _m49.start(), "v50 doit venir APRES v49"

_tail = SRC[_m50.start():]
_close = _tail.find("conn.close()")
assert _close != -1, "conn.close() final introuvable apres le bloc v50"
V50 = _tail[:_close + len("conn.close()")]


def _nocomment(block):
    return "\n".join(
        line for line in block.splitlines() if not line.lstrip().startswith("#")
    )


V50_NC = _nocomment(V50)
V50_LOW = V50_NC.lower()
V50_FLAT = re.sub(r"[\s\"']+", " ", V50_LOW)

print("-" * 64)
print("A. Ordre & présence")
check("Migration v49" in SRC and SRC.index("Migration v49") < _m50.start(),
      "1 v50 ajoutée APRÈS v49")

print("-" * 64)
print("B. Tables neuves")
for table in ("express_products", "express_consultations", "mini_game_sessions"):
    check(f"create table if not exists {table}" in V50_FLAT,
          f"2 CREATE TABLE IF NOT EXISTS {table}")
check("alter table accounts" not in V50_FLAT,
      "3 v50 ne touche JAMAIS accounts directement (débit/crédit passent par "
      "les primitives existantes, pas par une ALTER de migration)")

print("-" * 64)
print("C. Colonne additive memory_games.stars_awarded (jamais destructive)")
check("alter table memory_games add column if not exists stars_awarded" in V50_FLAT,
      "4 ADD COLUMN IF NOT EXISTS memory_games.stars_awarded")
check("drop column" not in V50_FLAT,
      "5 aucun DROP COLUMN (reward_seconds legacy conservée)")

print("-" * 64)
print("D. Anti-farming / idempotence EN BASE")
check(
    re.search(
        r"create unique index if not exists uq_express_consultations_user_idempotency "
        r"on express_consultations \(user_id, idempotency_key\)",
        V50_FLAT,
    ) is not None,
    "6 UNIQUE (user_id, idempotency_key) sur express_consultations",
)

print("-" * 64)
print("E. Aucun DROP/TRUNCATE/DELETE")
check("delete" not in V50_FLAT and "truncate" not in V50_FLAT
      and "drop table" not in V50_FLAT,
      "7 aucun DELETE / TRUNCATE / DROP TABLE dans le bloc v50")

print("-" * 64)
print("F. FK vers accounts, SANS CASCADE")
for table in ("express_consultations", "mini_game_sessions"):
    check(f"fk_{table}_account" in V50_FLAT, f"8 FK nommée fk_{table}_account")
check("on delete cascade" not in V50_FLAT, "9 aucun ON DELETE CASCADE")

print("-" * 64)
print("G. Seed express_products idempotent (jamais un DO UPDATE)")
check("on conflict (product_key) do nothing" in V50_FLAT,
      "10 seed express_products : ON CONFLICT DO NOTHING")
check(
    re.search(r"express_consultation_10min.*?500.*?600", V50_FLAT) is not None,
    "11 seed express_consultation_10min : 500 Étoiles -> 600 s",
)

print("-" * 64)
print("H. Activation GARDÉE de mini_game_completed (jamais un écrasement)")
check(
    re.search(
        r"update reward_rules set stars_amount=15, enabled=true, "
        r"daily_limit=1.*?where rule_key= ?mini_game_completed and enabled=false",
        V50_FLAT,
    ) is not None,
    "12 UPDATE reward_rules ... WHERE rule_key='mini_game_completed' AND "
    "enabled=FALSE (gardé — jamais un rejeu qui écraserait un réglage "
    "déjà personnalisé)",
)
check(V50_FLAT.count("update reward_rules") == 1,
      "13 UN SEUL UPDATE sur reward_rules dans le bloc v50")

print("-" * 64)
print("I. Idempotence de la migration elle-même")
check(V50_FLAT.count("if not exists") >= 4,
      "14 CREATE TABLE/INDEX/COLONNE utilisent IF NOT EXISTS")

print("-" * 64)
print("J. _ACCOUNT_DELETE_CHILD_TABLES mis à jour")
check("express_consultations" in A._ACCOUNT_DELETE_CHILD_TABLES,
      "15 express_consultations purgée à la suppression de compte")
check("mini_game_sessions" in A._ACCOUNT_DELETE_CHILD_TABLES,
      "16 mini_game_sessions purgée à la suppression de compte")
check("express_products" not in A._ACCOUNT_DELETE_CHILD_TABLES,
      "17 express_products ABSENTE (config globale)")

print("-" * 64)
total = _STATE["pass"] + _STATE["fail"]
print(f"RESULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko (sur {total})")
sys.exit(1 if _STATE["fail"] else 0)
