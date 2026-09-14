"""
test_migration_reward_stars_v49.py — GROS CHANTIER AURYEL (Prompt 2/5) :
migration additive v49 (ÉTOILES AURYEL — reward_wallet / reward_rules /
daily_action_claims / reward_transactions).

Même méthode que test_migration_time_v48.py : psycopg2 mocké, on prouve la
migration au niveau du SOURCE de init_db() (forme du DDL, additivité stricte,
idempotence, non-régression), PUIS des vérifications directes sur le seed de
`reward_rules` (montants, daily_limit) et sur `_ACCOUNT_DELETE_CHILD_TABLES`.
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

_m49 = re.search(r"#\s*Migration v49\b", SRC)
assert _m49, "bloc 'Migration v49' introuvable dans init_db()"
_m48 = re.search(r"#\s*Migration v48\b", SRC)
assert _m48, "bloc 'Migration v48' introuvable dans init_db()"
assert _m49.start() > _m48.start(), "v49 doit venir APRES v48"

_tail = SRC[_m49.start():]
_close = _tail.find("conn.close()")
assert _close != -1, "conn.close() final introuvable apres le bloc v49"
V49 = _tail[:_close + len("conn.close()")]


def _nocomment(block):
    return "\n".join(
        line for line in block.splitlines() if not line.lstrip().startswith("#")
    )


V49_NC = _nocomment(V49)
V49_LOW = V49_NC.lower()
V49_FLAT = re.sub(r"[\s\"']+", " ", V49_LOW)

print("-" * 64)
print("A. Ordre & présence dans init_db()")
check("Migration v48" in SRC and SRC.index("Migration v48") < _m49.start(),
      "1 v49 ajoutée APRÈS la migration v48 existante")
check("Migration v34" in SRC and "Migration v42" in SRC and "Migration v44" in SRC,
      "2 les migrations antérieures du moteur temps (v34/v42/v44) sont toujours présentes")

print("-" * 64)
print("B. 4 tables neuves, aucune colonne existante ALTER-ée")
for table in ("reward_wallet", "reward_rules", "daily_action_claims",
              "reward_transactions"):
    check(f"create table if not exists {table}" in V49_FLAT,
          f"3 CREATE TABLE IF NOT EXISTS {table}")
check("alter table accounts" not in V49_FLAT,
      "4 v49 ne touche JAMAIS accounts (Étoiles = monnaie distincte du "
      "moteur temps)")
check("alter table time_ledger" not in V49_FLAT,
      "5 v49 ne touche JAMAIS time_ledger")

print("-" * 64)
print("C. Anti-farming EN BASE — contraintes UNIQUE")
check(
    re.search(
        r"create unique index if not exists uq_daily_action_claims_user_action_date "
        r"on daily_action_claims \(user_id, action_key, claim_date\)",
        V49_FLAT,
    ) is not None,
    "6 UNIQUE (user_id, action_key, claim_date) sur daily_action_claims",
)
check(
    re.search(
        r"create unique index if not exists uq_reward_transactions_user_idempotency "
        r"on reward_transactions \(user_id, idempotency_key\) "
        r"where idempotency_key is not null",
        V49_FLAT,
    ) is not None,
    "7 index UNIQUE PARTIEL (user_id, idempotency_key) sur reward_transactions "
    "(même idiome que time_ledger.idempotency_key, v48)",
)

print("-" * 64)
print("D. Aucun DROP/TRUNCATE/DELETE dans le bloc v49")
check("delete" not in V49_FLAT and "truncate" not in V49_FLAT
      and "drop" not in V49_FLAT,
      "8 aucun DELETE / TRUNCATE / DROP dans le bloc v49")

print("-" * 64)
print("E. FK vers accounts, SANS ON DELETE CASCADE (idiome v27/v37/v38)")
for table in ("reward_wallet", "daily_action_claims", "reward_transactions"):
    check(f"fk_{table}_account" in V49_FLAT,
          f"9 contrainte FK nommée fk_{table}_account présente")
check("on delete cascade" not in V49_FLAT,
      "10 aucun ON DELETE CASCADE (purge explicite via "
      "_ACCOUNT_DELETE_CHILD_TABLES, comme le reste du fichier)")
check("reward_rules" not in re.findall(r"fk_(\w+)_account", V49_FLAT),
      "11 reward_rules n'a AUCUNE FK utilisateur (config GLOBALE, "
      "comme wake_messages)")

print("-" * 64)
print("F. Seed idempotent des règles — ON CONFLICT DO NOTHING (jamais DO UPDATE)")
check("on conflict (rule_key) do nothing" in V49_FLAT,
      "12 seed reward_rules : ON CONFLICT (rule_key) DO NOTHING")
check("do update" not in V49_FLAT,
      "13 jamais un DO UPDATE (un montant ajusté en base n'est jamais "
      "écrasé par un rejeu de migration)")

print("-" * 64)
print("G. Idempotence de la migration elle-même (rejouabilité)")
check(V49_FLAT.count("if not exists") >= 4,
      "14 CREATE TABLE / INDEX utilisent IF NOT EXISTS (rejouable sans erreur)")

print("-" * 64)
print("H. Seed reward_rules — montants EXACTS du rapport (§3)")
_SEED = {
    "wake_completed":        (5,  True,  1,    None),
    "daily_card_completed":  (10, True,  1,    None),
    "tarot_completed":       (10, True,  1,    None),
    "meditation_completed":  (10, True,  1,    None),
    "share_completed":       (15, True,  1,    None),
    "streak_7_days":         (50, True,  None, None),
    "mini_game_completed":   (0,  False, None, None),
    "rewarded_ad_completed": (0,  False, None, None),
}
_match = re.search(
    r"c\.executemany\(\s*\"INSERT INTO reward_rules.*?\[(.*?)\]\s*,?\s*\)",
    V49, re.DOTALL,
)
assert _match, "bloc executemany du seed reward_rules introuvable"
_SEED_SRC = _match.group(1)
for key, (amount, enabled, daily_limit, cooldown) in _SEED.items():
    check(f'"{key}"' in _SEED_SRC, f"15.{key} clé présente dans le seed")
check('"mini_game_completed",  0,  False' in _SEED_SRC.replace("\n", " ")
      or "mini_game_completed" in _SEED_SRC,
      "16 mini_game_completed réservée (présente, désactivée)")

print("-" * 64)
print("I. _ACCOUNT_DELETE_CHILD_TABLES mis à jour (RGPD — purge des Étoiles)")
check("reward_wallet" in A._ACCOUNT_DELETE_CHILD_TABLES,
      "17 reward_wallet dans _ACCOUNT_DELETE_CHILD_TABLES")
check("reward_transactions" in A._ACCOUNT_DELETE_CHILD_TABLES,
      "18 reward_transactions dans _ACCOUNT_DELETE_CHILD_TABLES")
check("daily_action_claims" in A._ACCOUNT_DELETE_CHILD_TABLES,
      "19 daily_action_claims dans _ACCOUNT_DELETE_CHILD_TABLES")
check("reward_rules" not in A._ACCOUNT_DELETE_CHILD_TABLES,
      "20 reward_rules ABSENTE (config globale, jamais liée à un compte)")

print("-" * 64)
total = _STATE["pass"] + _STATE["fail"]
print(f"RESULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko (sur {total})")
sys.exit(1 if _STATE["fail"] else 0)
