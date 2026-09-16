"""Régressions ciblées pour la migration Tarot quotidienne et son démarrage."""

import inspect
import os
import re
import sys
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()
for _key, _value in {
    "SECRET_KEY": "test-secret-key", "ADMIN_PASSWORD": "test",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "TAROT_MEDIA_UPLOAD_DISABLED": "1",
}.items():
    os.environ.setdefault(_key, _value)

import auryel_bot as A


def check(condition, label):
    if not condition:
        raise AssertionError(label)
    print(f"OK  {label}")


with open("migrations/034_tirages_daily_idempotency.sql", encoding="utf-8") as handle:
    migration = handle.read()

check("ADD COLUMN IF NOT EXISTS draw_date DATE" in migration,
      "034 ajoute draw_date sans réécrire les historiques")
check("CREATE UNIQUE INDEX IF NOT EXISTS uq_tirages_user_draw_date" in migration,
      "034 crée l'index unique attendu")
check("WHERE draw_date IS NOT NULL" in migration,
      "034 conserve les anciennes lignes NULL hors unicité")
check(not re.search(r"\b(DROP|TRUNCATE|DELETE|UPDATE)\b", migration, re.I),
      "034 ne supprime ni ne modifie les lignes existantes")

source = inspect.getsource(A.init_db)
v61 = source[source.index("# Migration v61"):]
check("034_tirages_daily_idempotency.sql" in v61,
      "init_db exécute bien la migration 034")
check("raise CriticalSchemaMigrationError" in v61,
      "échec v61 transformé en erreur de schéma critique")
module_source = inspect.getsource(A)
startup = module_source[module_source.index("try:\n    init_db()"):
                       module_source.index("# ============================================================\n# 150 PSAUMES")]
check("except CriticalSchemaMigrationError" in startup
      and "raise" in startup.split("except CriticalSchemaMigrationError", 1)[1].split(
          "except Exception", 1)[0],
      "l'initialisation ne masque plus l'échec critique v61")

check("CREATE UNIQUE INDEX IF NOT EXISTS uq_tirages_user_draw_date" in migration
      and "pgcode" in inspect.getsource(A.save_tirage)
      and "23505" in inspect.getsource(A.save_tirage),
      "la concurrence est arbitrée par PostgreSQL puis rejouée sur le gagnant")
check("_reconcile_wellbeing_progress(" not in inspect.getsource(A.api_tirages_create),
      "POST Tarot ne déclenche aucune récompense Bien-être")
check("per_mission[\"tirage\"]" not in inspect.getsource(A._reconcile_wellbeing_progress)
      and "per_mission[\"tirage\"]" not in inspect.getsource(A._wellbeing_progress_payload),
      "aucun crédit Bien-être ne dépend indirectement d'un tirage")

print("RÉSULTAT : 11 ok / 0 ko")
