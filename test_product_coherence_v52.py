"""Contrats économiques du lot v52.

Ce test reste sans base réelle : il vérifie les autorités de configuration et
le DDL de migration avant tout déploiement Railway.
"""

import inspect
import os
import re
import sys
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

for _k, _v in {
    "SECRET_KEY": "test-secret-key",
    "ADMIN_PASSWORD": "test",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
}.items():
    os.environ.setdefault(_k, _v)

import auryel_bot as A


def check(condition, label):
    if not condition:
        raise AssertionError(label)
    print(f"✅ {label}")


source = inspect.getsource(A.init_db)
check(A.FIRST_FREE_SECONDS == 1200, "cadeau de bienvenue = 20 minutes")
check(A.PREMIUM_MONTHLY_SECONDS == 14400, "Premium = 4 heures par mois")
check(A._QUOTA_SHIM_MONTHLY_LIMIT_HOURS == 4, "quota de compatibilité = 4 heures")
check("SET DEFAULT 1200" in source, "les nouveaux comptes reçoivent 1200 secondes")
check("SET DEFAULT 14400" in source, "les nouvelles périodes reçoivent 14400 secondes")
check(
    "SET monthly_allowance_seconds=14400" in source
    and "WHERE monthly_allowance_seconds=28800" in source,
    "les anciennes allowances 8 h sont réalignées sans toucher aux autres buckets",
)
check(
    "first_free_seconds_remaining=1200" not in source,
    "la migration ne recrédite pas le cadeau déjà consommé",
)
print("Tous les contrats v52 sont valides.")
