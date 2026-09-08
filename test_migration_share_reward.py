"""
test_migration_share_reward.py — J5 : migration additive v37 (récompense
partage). Table `share_reward_days` (PK user_id+share_date) + colonne
`accounts.share_reward_credited_at` NULLABLE + FK idempotente.

Comme pour test_migration_v27.py : le harnais ne joue jamais init_db() contre
une vraie base (psycopg2 mocké). On prouve la migration au niveau du SOURCE de
init_db() ET du miroir lisible migrations/011_share_reward.sql : forme du DDL,
additivité stricte, idempotence, non-régression sur v34..v36. Script, pas pytest.
"""

import sys
import inspect
import re
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

import os
for _k, _v in {
    "SECRET_KEY": "test-secret-key", "VERIFY_TOKEN": "test", "ADMIN_PASSWORD": "test",
    "CRON_SECRET": "test", "DATABASE_URL": "postgresql://test:test@localhost/test",
    "STRIPE_SK": "sk_test_placeholder", "STRIPE_WEBHOOK_SECRET": "whsec_placeholder",
    "WHATSAPP_TOKEN": "test", "PHONE_NUMBER_ID": "test", "GROQ_API_KEY": "test",
    "RESEND_API_KEY": "test", "META_APP_SECRET": "test", "DAILY_SECRET": "test",
    "SEO_SECRET": "test", "TAROT_MEDIA_UPLOAD_DISABLED": "1",
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

# --- Isolation de la tranche v37 : du marqueur "# Migration v37" au conn.close()
# final de init_db() (v37 est la dernière migration). Si une v38 apparaît un
# jour, la tranche s'arrêtera à son marqueur.
_m37 = re.search(r"#\s*Migration v37\b", SRC)
assert _m37, "bloc 'Migration v37' introuvable dans init_db()"
_m38 = re.search(r"#\s*Migration v38\b", SRC)
if _m38:
    assert _m38.start() > _m37.start(), "v38 doit venir APRÈS v37"
    V37 = SRC[_m37.start():_m38.start()]
else:
    _rest = SRC[_m37.start():]
    _close = _rest.find("conn.close()")
    assert _close != -1, "conn.close() final introuvable après le bloc v37"
    V37 = _rest[:_close + len("conn.close()")]


def _nocomment(block):
    return "\n".join(
        line for line in block.splitlines() if not line.lstrip().startswith("#")
    )


V37_NOCOMMENT = _nocomment(V37)
V37_LOW = V37_NOCOMMENT.lower()
V37_FLAT = re.sub(r"[\s\"']+", " ", V37_LOW)

# --- bloc FK isolé (Migration v37 (suite)).
_fk_start = V37.find("Migration v37 (suite)")
assert _fk_start != -1, "bloc FK 'Migration v37 (suite)' introuvable"
FK = V37[_fk_start:]
FK_NOCOMMENT = _nocomment(FK)
FK_LOW = FK_NOCOMMENT.lower()
FK_FLAT = re.sub(r"\s+", " ", FK_LOW)

print("-" * 64)
print("A. Ordre & présence dans init_db()")

check("Migration v36" in SRC and SRC.index("Migration v36") < _m37.start(),
      "1 v37 ajoutée APRÈS la migration v36 existante")
check("Migration v34" in SRC and SRC.index("Migration v34") < _m37.start(),
      "2 v37 après v34 (schéma du compteur de temps) aussi")

print("-" * 64)
print("B. Création de share_reward_days")

check(re.search(r"create\s+table\s+if\s+not\s+exists\s+share_reward_days", V37_LOW)
      is not None,
      "3 CREATE TABLE IF NOT EXISTS share_reward_days")
for _rx, _lbl in [
    (r"user_id\s+uuid\s+not\s+null", "user_id UUID NOT NULL"),
    (r"share_date\s+date\s+not\s+null", "share_date DATE NOT NULL"),
    (r"created_at\s+timestamptz\s+not\s+null\s+default\s+now\(\)",
     "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
]:
    check(re.search(_rx, V37_LOW) is not None, f"4 colonne : {_lbl}")

check(re.search(r"primary\s+key\s*\(\s*user_id\s*,\s*share_date\s*\)", V37_LOW)
      is not None,
      "5 PRIMARY KEY (user_id, share_date) -> unicité « 1 jour max / jour » EN BASE")

print("-" * 64)
print("C. Colonne accounts.share_reward_credited_at")

check("alter table accounts add column if not exists share_reward_credited_at timestamptz"
      in V37_FLAT,
      "6 ALTER TABLE accounts ADD COLUMN IF NOT EXISTS share_reward_credited_at TIMESTAMPTZ")
check(V37_LOW.count("alter table accounts") == 1,
      "7 un seul ALTER TABLE accounts (aucune autre colonne accounts touchée)")
check(re.search(r"share_reward_credited_at\s+timestamptz\s+default", V37_LOW) is None,
      "8 la colonne credited_at n'a AUCUN DEFAULT (NULL = pas encore crédité)")

print("-" * 64)
print("D. FK share_reward_days.user_id -> accounts(user_id), idempotente")

check("do $$" in FK_LOW and "begin" in FK_LOW and "end" in FK_LOW,
      "9 FK ajoutée via un bloc DO PL/pgSQL (idempotence côté PostgreSQL)")
check("alter table share_reward_days add constraint fk_share_reward_days_account"
      in FK_FLAT
      and "foreign key (user_id) references accounts(user_id)" in FK_FLAT,
      "10 le DO tente ADD CONSTRAINT ... FOREIGN KEY (user_id) REFERENCES accounts(user_id)")
_when = re.findall(r"\bwhen\s+([a-z_]+)", FK_LOW)
check(_when == ["duplicate_object"],
      f"11 EXCEPTION ne gère QUE duplicate_object (trouvé : {_when or 'aucun'})")
check("when others" not in FK_LOW,
      "12 aucun WHEN OTHERS (catch-all) qui avalerait une autre erreur")
check(re.search(r"when\s+duplicate_object\s+then\s+null", FK_LOW) is not None,
      "13 duplicate_object -> NULL (no-op) : rejeu sans erreur ni effet de bord")
check("on delete cascade" not in FK_LOW,
      "14 FK SANS ON DELETE CASCADE (DELETE /api/app/account purge explicitement)")
check(re.search(r"\bexcept\b", FK_LOW) is None,
      "15 aucun try/except Python autour du bloc FK (idempotence 100 % SQL)")
check("print(" not in FK_NOCOMMENT and "conn.rollback()" not in FK_NOCOMMENT,
      "16 aucun print / rollback masquant dans le bloc FK")

print("-" * 64)
print("E. Additivité stricte de v37")

check("drop" not in V37_LOW, "17 aucun DROP dans la tranche v37")
check("truncate" not in V37_LOW, "18 aucun TRUNCATE dans la tranche v37")
check("update " not in V37_LOW and "delete " not in V37_LOW,
      "19 v37 : aucun UPDATE / DELETE (pas de backfill, pas de mutation)")
check("insert into" not in V37_LOW,
      "20 v37 : aucun INSERT (aucune donnée écrite par la migration)")
check(not re.search(r"\busers\b", V37_LOW),
      "21 v37 ne référence jamais la table legacy users")
check("consultation_allowance" not in V37_LOW and "consultations" not in V37_LOW
      and "earned_credits" not in V37_LOW and "mobile_subscriptions" not in V37_LOW
      and "messages" not in V37_LOW,
      "22 v37 ne touche QUE share_reward_days (+ 1 colonne accounts)")
check("if not exists" in V37_LOW
      and V37_LOW.count("if not exists") == 2,
      "23 idempotence : 2x IF NOT EXISTS (CREATE TABLE + ADD COLUMN)")

print("-" * 64)
print("F. Miroir lisible migrations/011_share_reward.sql")

with open(os.path.join(os.path.dirname(__file__), "migrations",
                       "011_share_reward.sql"), encoding="utf-8") as _f:
    MIR_RAW = _f.read()
MIR_LOW = MIR_RAW.lower()
MIR_FLAT = re.sub(r"[\s\"']+", " ", MIR_LOW)
# DDL seul (lignes de commentaire SQL `--` retirées : l'en-tête cite pour
# documentation la requête UPDATE de la route, ce n'est pas du DDL de migration).
MIR_DDL_LOW = "\n".join(
    ln for ln in MIR_LOW.splitlines() if not ln.lstrip().startswith("--")
)
MIR_DDL_FLAT = re.sub(r"[\s\"']+", " ", MIR_DDL_LOW)

check("create table if not exists share_reward_days" in MIR_LOW,
      "24 miroir : CREATE TABLE IF NOT EXISTS share_reward_days")
check("primary key (user_id, share_date)" in MIR_FLAT,
      "25 miroir : PRIMARY KEY (user_id, share_date)")
check("alter table accounts add column if not exists share_reward_credited_at timestamptz"
      in MIR_FLAT,
      "26 miroir : ADD COLUMN IF NOT EXISTS share_reward_credited_at TIMESTAMPTZ")
check("add constraint fk_share_reward_days_account" in MIR_FLAT
      and "foreign key (user_id) references accounts(user_id)" in MIR_FLAT,
      "27 miroir : FK nommée fk_share_reward_days_account")
check("when duplicate_object then null" in MIR_FLAT,
      "28 miroir : DO $$ idempotent (duplicate_object -> NULL)")
check("drop" not in MIR_DDL_LOW and "truncate" not in MIR_DDL_LOW
      and "insert into" not in MIR_DDL_LOW
      and " update " not in MIR_DDL_FLAT and " delete " not in MIR_DDL_FLAT,
      "29 miroir : DDL strictement additif (aucun DROP/TRUNCATE/INSERT/UPDATE/DELETE)")

print("-" * 64)
print("G. Non-régression : v34..v36 intactes")

check("Migration v34" in SRC and "Migration v35" in SRC and "Migration v36" in SRC,
      "30 v34 / v35 / v36 toujours présentes dans init_db()")
check("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS password_hash TEXT" in SRC,
      "31 v36 (password_hash) intacte, non réécrite")
check("share_reward_credited_at" not in SRC[:_m37.start()],
      "32 la colonne share_reward_credited_at n'apparaît QUE dans la tranche v37")

print("-" * 64)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
