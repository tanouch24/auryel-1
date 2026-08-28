"""
test_migration_v27.py — B1 billing : migration additive v27 (table
mobile_subscriptions pour les abonnements Google Play / Apple).

Le harnais de test du projet ne joue jamais init_db() contre une vraie base
(psycopg2 mocké, get_conn -> FakeConn/FakeCursor par SQL exact). On prouve donc
la migration au niveau du SOURCE de init_db() : forme du DDL, additivité stricte,
idempotence, et non-régression sur consultation_allowance / le legacy Stripe.
Style aligné sur test_consultation_engine.py / test_auth.py (script, pas pytest).
"""

import sys
import inspect
import re
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

import os
for _k, _v in {
    "SECRET_KEY": "test-secret-key",
    "VERIFY_TOKEN": "test",
    "ADMIN_PASSWORD": "test",
    "CRON_SECRET": "test",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "STRIPE_SK": "sk_test_placeholder",
    "STRIPE_WEBHOOK_SECRET": "whsec_placeholder",
    "WHATSAPP_TOKEN": "test",
    "PHONE_NUMBER_ID": "test",
    "GROQ_API_KEY": "test",
    "RESEND_API_KEY": "test",
    "META_APP_SECRET": "test",
    "DAILY_SECRET": "test",
    "SEO_SECRET": "test",
    "TAROT_MEDIA_UPLOAD_DISABLED": "1",
}.items():
    os.environ.setdefault(_k, _v)

import auryel_bot as A

_STATE = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _STATE["pass"] += 1
        print(f"✅ {label}")
    else:
        _STATE["fail"] += 1
        print(f"❌ {label}")


SRC = inspect.getsource(A.init_db)

# --- Isolation de la tranche v27 : du marqueur "# Migration v27" au conn.close()
# final de init_db(). Tout le reste de init_db() (v1..v26) est hors périmètre.
_m = re.search(r"#\s*Migration v27\b", SRC)
assert _m, "bloc 'Migration v27' introuvable dans init_db()"
V27 = SRC[_m.start():]
_close = V27.find("conn.close()")
assert _close != -1, "conn.close() final introuvable après le bloc v27"
V27 = V27[:_close + len("conn.close()")]

V27_NOCOMMENT = "\n".join(
    line for line in V27.splitlines() if not line.lstrip().startswith("#")
)
V27_LOW = V27_NOCOMMENT.lower()

print("-" * 64)
print("A. Ordre & présence")

check("Migration v26" in SRC and SRC.index("Migration v26") < _m.start(),
      "1 v27 ajoutée APRÈS la migration v26 existante")
check("Migration v25" in SRC and SRC.index("Migration v25") < _m.start(),
      "2 v27 après v25 (schéma consultations) aussi")

print("-" * 64)
print("B. Création de mobile_subscriptions")

check(re.search(r"create\s+table\s+if\s+not\s+exists\s+mobile_subscriptions",
                V27_LOW) is not None,
      "3 CREATE TABLE IF NOT EXISTS mobile_subscriptions")

_required_cols = [
    (r"id\s+uuid\s+primary\s+key", "id UUID PRIMARY KEY"),
    (r"user_id\s+uuid\s+not\s+null", "user_id UUID NOT NULL"),
    (r"store\s+text\s+not\s+null", "store TEXT NOT NULL"),
    (r"product_id\s+text\s+not\s+null", "product_id TEXT NOT NULL"),
    (r"subscription_key\s+text\s+not\s+null", "subscription_key TEXT NOT NULL"),
    (r"latest_transaction_id\s+text", "latest_transaction_id TEXT"),
    (r"status\s+text\s+not\s+null", "status TEXT NOT NULL"),
    (r"purchased_at\s+timestamptz", "purchased_at TIMESTAMPTZ"),
    (r"expires_at\s+timestamptz", "expires_at TIMESTAMPTZ"),
    (r"auto_renewing\s+boolean", "auto_renewing BOOLEAN"),
    (r"last_verified_at\s+timestamptz", "last_verified_at TIMESTAMPTZ"),
    (r"raw_payload\s+jsonb", "raw_payload JSONB"),
    (r"created_at\s+timestamptz\s+not\s+null\s+default\s+now\(\)",
     "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
    (r"updated_at\s+timestamptz\s+not\s+null\s+default\s+now\(\)",
     "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
]
for _rx, _lbl in _required_cols:
    check(re.search(_rx, V27_LOW) is not None, f"4 colonne : {_lbl}")

print("-" * 64)
print("C. Contraintes")

check(re.search(r"unique\s*\(\s*store\s*,\s*subscription_key\s*\)", V27_LOW)
      is not None,
      "5 UNIQUE (store, subscription_key)")
check(re.search(
        r"foreign\s+key\s*\(\s*user_id\s*\)\s*references\s+accounts\s*\(\s*user_id\s*\)",
        V27_LOW) is not None,
      "6 FK user_id -> accounts(user_id)")
check("add constraint" in V27_LOW and "fk_mobile_subscriptions_account" in V27_LOW,
      "7 FK posée via ADD CONSTRAINT nommée (bloc séparé)")

print("-" * 64)
print("D. Idempotence de la table")

check("if not exists" in V27_LOW, "8 CREATE TABLE porte IF NOT EXISTS")

# --- Tranche FK isolée : du marqueur "Migration v27 (suite)" à la fin de V27.
_fk_start = V27.find("Migration v27 (suite)")
assert _fk_start != -1, "bloc FK 'Migration v27 (suite)' introuvable"
FK = V27[_fk_start:]
FK_NOCOMMENT = "\n".join(
    line for line in FK.splitlines() if not line.lstrip().startswith("#")
)
FK_LOW = FK_NOCOMMENT.lower()
# espaces normalisés pour les regex SQL
FK_FLAT = re.sub(r"\s+", " ", FK_LOW)

print("-" * 64)
print("D2. FK mobile_subscriptions.user_id -> accounts(user_id) : idempotence explicite")

check("do $$" in FK_LOW and "begin" in FK_LOW and "end" in FK_LOW,
      "9 la FK est ajoutée via un bloc DO PL/pgSQL (idempotence côté PostgreSQL)")
check("alter table mobile_subscriptions add constraint fk_mobile_subscriptions_account"
      in FK_FLAT
      and "foreign key (user_id) references accounts(user_id)" in FK_FLAT,
      "10 le DO tente bien ADD CONSTRAINT ... FOREIGN KEY (user_id) REFERENCES accounts(user_id)")

# Le SEUL cas d'exception géré doit être duplicate_object (contrainte déjà là au rejeu).
_when_conditions = re.findall(r"\bwhen\s+([a-z_]+)", FK_LOW)
check(_when_conditions == ["duplicate_object"],
      f"11 EXCEPTION ne gère QUE duplicate_object (trouvé : {_when_conditions or 'aucun'})")
check("when others" not in FK_LOW,
      "12 aucun WHEN OTHERS (catch-all PL/pgSQL) qui avalerait une autre erreur")
check(re.search(r"when\s+duplicate_object\s+then\s+null", FK_LOW) is not None,
      "13 duplicate_object -> NULL (no-op) : rejeu sans erreur, sans effet de bord")

# Aucun try/except PYTHON autour de la FK : une vraie erreur (undefined_table,
# undefined_column, wrong_object_type…) doit remonter, pas être transformée en print.
# NB : `\bexcept\b` ne matche PAS le mot SQL "exception" (pas de frontière t|i).
check(re.search(r"\bexcept\s+exception\b", FK_LOW) is None,
      "14 AUCUN `except Exception` général autour de l'ajout FK")
check(re.search(r"\bexcept\b", FK_LOW) is None,
      "15 aucun try/except Python sur le bloc FK (idempotence 100 % SQL)")
check("conn.rollback()" not in FK_NOCOMMENT,
      "16 pas de rollback masquant : une erreur non-doublon propage au lieu d'être avalée")
check(FK_NOCOMMENT.count("conn.commit()") == 1,
      "17 un seul commit après le DO (FK persistée au 1er boot)")
check("print(" not in FK_NOCOMMENT,
      "18 aucun print() d'erreur : une erreur non-doublon n'est PAS silencieusement loggée puis ignorée")

print("-" * 64)
print("E. Additivité stricte — rien d'autre touché")

check("drop" not in V27_LOW, "19 aucun DROP dans la tranche v27")
check("consultation_allowance" not in V27_LOW,
      "20 v27 ne référence jamais consultation_allowance")
check("earned_credits" not in V27_LOW,
      "21 v27 ne référence jamais earned_credits")
check(not re.search(r"\busers\b", V27_LOW),
      "22 v27 ne référence jamais la table legacy users")
check("stripe" not in V27_LOW and "abonne" not in V27_LOW,
      "23 v27 ne touche rien de Stripe / abonne")
check("alter table users" not in V27_LOW and "alter table accounts" not in V27_LOW,
      "24 v27 n'ALTER aucune table existante")
check("update " not in V27_LOW and "delete " not in V27_LOW,
      "25 v27 ne fait aucun UPDATE / DELETE (pas de backfill, pas de mutation)")
check("insert into" not in V27_LOW,
      "26 v27 ne fait aucun INSERT (aucune donnée écrite par la migration)")

print("-" * 64)
print("F. Non-régression : le reste de init_db() est inchangé")

check("ALTER TABLE consultation_allowance ALTER COLUMN monthly_limit SET DEFAULT 10"
      in SRC,
      "27 v26 (DEFAULT 10) toujours présente, intacte")
check("UPDATE consultation_allowance SET monthly_limit = 10 WHERE monthly_limit = 4"
      in SRC,
      "28 v26 (UPDATE 4->10) toujours présente, intacte")
# La FK v25 (consultations) garde son idiome try/except historique : on ne l'a pas
# touchée, seul le bloc FK de v27 a été durci.
check(SRC.count("except Exception as e:") >= 1 and "Migration v25 (FK consultations)" in SRC,
      "29 idiome historique des migrations antérieures (v25) laissé intact")

print("-" * 64)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
