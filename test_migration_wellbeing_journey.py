"""
test_migration_wellbeing_journey.py — J7 : migration additive v38 (« Mon
parcours bien-être »). Tables `wellbeing_mission_days`
(PK user_id+day_date+mission_id) et `wellbeing_cycle_rewards`
(PK user_id+cycle_number) + 2 FK idempotentes vers accounts(user_id).

Comme test_migration_share_reward.py : psycopg2 mocké, aucune vraie base. On
prouve la migration au niveau du SOURCE de init_db() ET du miroir lisible
migrations/012_wellbeing_journey.sql : forme du DDL, additivité stricte,
idempotence, non-régression sur v34..v37. Script, pas pytest.
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

# --- Isolation de la tranche v38 : du marqueur "# Migration v38" au conn.close()
# final de init_db() (v38 est la dernière migration).
_m38 = re.search(r"#\s*Migration v38\b", SRC)
assert _m38, "bloc 'Migration v38' introuvable dans init_db()"
_m39 = re.search(r"#\s*Migration v39\b", SRC)
if _m39:
    assert _m39.start() > _m38.start(), "v39 doit venir APRÈS v38"
    V38 = SRC[_m38.start():_m39.start()]
else:
    _rest = SRC[_m38.start():]
    _close = _rest.rfind("conn.close()")
    assert _close != -1, "conn.close() final introuvable après le bloc v38"
    V38 = _rest[:_close + len("conn.close()")]


def _nocomment(block):
    return "\n".join(
        line for line in block.splitlines() if not line.lstrip().startswith("#")
    )


V38_NOCOMMENT = _nocomment(V38)
V38_LOW = V38_NOCOMMENT.lower()
V38_FLAT = re.sub(r"[\s\"']+", " ", V38_LOW)

print("-" * 64)
print("A. Ordre & présence dans init_db()")

check("Migration v37" in SRC and SRC.index("Migration v37") < _m38.start(),
      "1 v38 ajoutée APRÈS la migration v37 (récompense partage)")
check("Migration v34" in SRC and SRC.index("Migration v34") < _m38.start(),
      "2 v38 après v34 (schéma du compteur de temps) aussi")

print("-" * 64)
print("B. Création de wellbeing_mission_days")

check(re.search(r"create\s+table\s+if\s+not\s+exists\s+wellbeing_mission_days",
                V38_LOW) is not None,
      "3 CREATE TABLE IF NOT EXISTS wellbeing_mission_days")
for _rx, _lbl in [
    (r"user_id\s+uuid\s+not\s+null", "user_id UUID NOT NULL"),
    (r"day_date\s+date\s+not\s+null", "day_date DATE NOT NULL"),
    (r"mission_id\s+text\s+not\s+null", "mission_id TEXT NOT NULL"),
    (r"created_at\s+timestamptz\s+not\s+null\s+default\s+now\(\)",
     "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
]:
    check(re.search(_rx, V38_LOW) is not None, f"4 colonne : {_lbl}")
check(re.search(r"primary\s+key\s*\(\s*user_id\s*,\s*day_date\s*,\s*mission_id\s*\)",
                V38_LOW) is not None,
      "5 PRIMARY KEY (user_id, day_date, mission_id) -> « 1 fois / jour / mission » EN BASE")
check("create index if not exists idx_wellbeing_mission_days_user" in V38_FLAT,
      "6 index (user_id, day_date), IF NOT EXISTS")

print("-" * 64)
print("C. Création de wellbeing_cycle_rewards")

check(re.search(r"create\s+table\s+if\s+not\s+exists\s+wellbeing_cycle_rewards",
                V38_LOW) is not None,
      "7 CREATE TABLE IF NOT EXISTS wellbeing_cycle_rewards")
for _rx, _lbl in [
    (r"cycle_number\s+integer\s+not\s+null", "cycle_number INTEGER NOT NULL"),
    (r"credited_at\s+timestamptz\s+not\s+null\s+default\s+now\(\)",
     "credited_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
    (r"credited_seconds\s+integer\s+not\s+null\s+default\s+900",
     "credited_seconds INTEGER NOT NULL DEFAULT 900"),
]:
    check(re.search(_rx, V38_LOW) is not None, f"8 colonne : {_lbl}")
check(re.search(r"primary\s+key\s*\(\s*user_id\s*,\s*cycle_number\s*\)", V38_LOW)
      is not None,
      "9 PRIMARY KEY (user_id, cycle_number) -> 1 récompense max par cycle EN BASE")

print("-" * 64)
print("D. FK -> accounts(user_id), idempotentes, SANS CASCADE")

check(V38_LOW.count("do $$") == 2 and V38_LOW.count("begin") >= 2,
      "10 les 2 FK ajoutées via des blocs DO PL/pgSQL")
check("alter table wellbeing_mission_days add constraint fk_wellbeing_mission_days_account"
      in V38_FLAT
      and "alter table wellbeing_cycle_rewards add constraint fk_wellbeing_cycle_rewards_account"
      in V38_FLAT,
      "11 2 ADD CONSTRAINT nommées (fk_wellbeing_mission_days_account / _cycle_rewards_account)")
check(V38_FLAT.count("foreign key (user_id) references accounts(user_id)") == 2,
      "12 les 2 FK pointent vers accounts(user_id)")
_when = re.findall(r"\bwhen\s+([a-z_]+)", V38_LOW)
check(_when == ["duplicate_object", "duplicate_object"],
      f"13 EXCEPTION ne gère QUE duplicate_object (trouvé : {_when or 'aucun'})")
check("when others" not in V38_LOW,
      "14 aucun WHEN OTHERS (catch-all)")
check("on delete cascade" not in V38_LOW,
      "15 FK SANS ON DELETE CASCADE (DELETE /api/app/account purge explicitement)")

print("-" * 64)
print("E. Additivité stricte de v38")

check("drop" not in V38_LOW, "16 aucun DROP dans la tranche v38")
check("truncate" not in V38_LOW, "17 aucun TRUNCATE dans la tranche v38")
check(" update " not in V38_FLAT and " delete " not in V38_FLAT,
      "18 v38 : aucun UPDATE / DELETE (pas de backfill, pas de mutation)")
check("insert into" not in V38_LOW,
      "19 v38 : aucun INSERT (aucune donnée écrite par la migration)")
check("alter table accounts" not in V38_LOW,
      "20 v38 ne touche AUCUNE colonne de accounts (crédit via une route, pas la migration)")
check(not re.search(r"\busers\b", V38_LOW),
      "21 v38 ne référence jamais la table legacy users")
check("share_reward_days" not in V38_LOW and "consultations" not in V38_LOW
      and "tirages" not in V38_LOW and "messages" not in V38_LOW,
      "22 v38 ne crée QUE wellbeing_mission_days / wellbeing_cycle_rewards")
check(V38_LOW.count("if not exists") == 3,
      "23 idempotence : 3x IF NOT EXISTS (2 CREATE TABLE + 1 CREATE INDEX)")

print("-" * 64)
print("F. Miroir lisible migrations/012_wellbeing_journey.sql")

with open(os.path.join(os.path.dirname(__file__), "migrations",
                       "012_wellbeing_journey.sql"), encoding="utf-8") as _f:
    MIR_RAW = _f.read()
MIR_LOW = MIR_RAW.lower()
MIR_FLAT = re.sub(r"[\s\"']+", " ", MIR_LOW)
MIR_DDL_LOW = "\n".join(
    ln for ln in MIR_LOW.splitlines() if not ln.lstrip().startswith("--")
)
MIR_DDL_FLAT = re.sub(r"[\s\"']+", " ", MIR_DDL_LOW)

check("create table if not exists wellbeing_mission_days" in MIR_LOW,
      "24 miroir : CREATE TABLE IF NOT EXISTS wellbeing_mission_days")
check("primary key (user_id, day_date, mission_id)" in MIR_FLAT,
      "25 miroir : PRIMARY KEY (user_id, day_date, mission_id)")
check("create table if not exists wellbeing_cycle_rewards" in MIR_LOW
      and "primary key (user_id, cycle_number)" in MIR_FLAT,
      "26 miroir : wellbeing_cycle_rewards + PRIMARY KEY (user_id, cycle_number)")
check("add constraint fk_wellbeing_mission_days_account" in MIR_FLAT
      and "add constraint fk_wellbeing_cycle_rewards_account" in MIR_FLAT
      and MIR_FLAT.count("foreign key (user_id) references accounts(user_id)") == 2,
      "27 miroir : 2 FK nommées vers accounts(user_id)")
check(MIR_FLAT.count("when duplicate_object then null") == 2,
      "28 miroir : 2 DO $$ idempotents (duplicate_object -> NULL)")
check("drop" not in MIR_DDL_LOW and "truncate" not in MIR_DDL_LOW
      and "insert into" not in MIR_DDL_LOW
      and " update " not in MIR_DDL_FLAT and " delete " not in MIR_DDL_FLAT,
      "29 miroir : DDL strictement additif (aucun DROP/TRUNCATE/INSERT/UPDATE/DELETE)")

print("-" * 64)
print("G. Non-régression : v34..v37 intactes + suppression compte")

check("Migration v34" in SRC and "Migration v36" in SRC and "Migration v37" in SRC,
      "30 v34 / v36 / v37 toujours présentes dans init_db()")
check("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS share_reward_credited_at TIMESTAMPTZ" in SRC,
      "31 v37 (share_reward_credited_at) intacte, non réécrite")
check("wellbeing_mission_days" not in SRC[:_m38.start()]
      and "wellbeing_cycle_rewards" not in SRC[:_m38.start()],
      "32 les tables wellbeing n'apparaissent QUE dans la tranche v38")
_DEL = tuple(A._ACCOUNT_DELETE_CHILD_TABLES)
check("wellbeing_mission_days" in _DEL and "wellbeing_cycle_rewards" in _DEL,
      "33 DELETE /api/app/account purge les 2 nouvelles tables (FK -> accounts)")
check("share_reward_days" in _DEL and "app_sessions" in _DEL and "accounts" not in _DEL,
      "34 non-régression : share_reward_days / app_sessions toujours dans la purge")

print("-" * 64)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
