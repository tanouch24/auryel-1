"""
test_migration_v27.py — billing : migrations additives v27 (table
mobile_subscriptions pour les abonnements Google Play / Apple) et v28
(colonne current_period_start).

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

# --- Isolation de la tranche v27 : du marqueur "# Migration v27" au marqueur
# "# Migration v28" (ou, à défaut, au conn.close() final). Le reste de
# init_db() (v1..v26) est hors périmètre.
_m = re.search(r"#\s*Migration v27\b", SRC)
assert _m, "bloc 'Migration v27' introuvable dans init_db()"
_m28 = re.search(r"#\s*Migration v28\b", SRC)
assert _m28, "bloc 'Migration v28' introuvable dans init_db()"
_m29 = re.search(r"#\s*Migration v29\b", SRC)
assert _m29, "bloc 'Migration v29' introuvable dans init_db()"
_m30 = re.search(r"#\s*Migration v30\b", SRC)
assert _m30, "bloc 'Migration v30' introuvable dans init_db()"
assert _m28.start() > _m.start(), "v28 doit venir APRÈS v27"
assert _m29.start() > _m28.start(), "v29 doit venir APRÈS v28"
assert _m30.start() > _m29.start(), "v30 doit venir APRÈS v29"

V27 = SRC[_m.start():_m28.start()]
V28 = SRC[_m28.start():_m29.start()]
V29 = SRC[_m29.start():_m30.start()]

# --- Isolation de la tranche v30 : du marqueur "# Migration v30" au conn.close()
# final de init_db().
_v30 = SRC[_m30.start():]
_close30 = _v30.find("conn.close()")
assert _close30 != -1, "conn.close() final introuvable après le bloc v30"
V30 = _v30[:_close30 + len("conn.close()")]


def _nocomment(block):
    return "\n".join(
        line for line in block.splitlines() if not line.lstrip().startswith("#")
    )


V27_NOCOMMENT = _nocomment(V27)
V27_LOW = V27_NOCOMMENT.lower()
V28_NOCOMMENT = _nocomment(V28)
V28_LOW = V28_NOCOMMENT.lower()
V29_NOCOMMENT = _nocomment(V29)
V29_LOW = V29_NOCOMMENT.lower()
V30_NOCOMMENT = _nocomment(V30)
V30_LOW = V30_NOCOMMENT.lower()

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
print("G. Migration v28 — colonne current_period_start (billing B2.2)")

check(_m28.start() > _m.start(), "30 v28 présente et placée APRÈS v27 dans init_db()")
# La requête peut être écrite en littéraux Python concaténés : on aplatit les
# guillemets et espaces pour retrouver le SQL logique.
V28_FLAT = re.sub(r"[\s\"']+", " ", V28_LOW)
check("alter table mobile_subscriptions add column if not exists "
      "current_period_start timestamptz" in V28_FLAT,
      "31 ALTER TABLE mobile_subscriptions ADD COLUMN IF NOT EXISTS "
      "current_period_start TIMESTAMPTZ")
check("if not exists" in V28_LOW,
      "32 ADD COLUMN IF NOT EXISTS -> migration rejouable sans erreur")
check("drop" not in V28_LOW,
      "33 aucun DROP dans la tranche v28")
check("update " not in V28_LOW and "delete " not in V28_LOW
      and "insert into" not in V28_LOW,
      "34 v28 : aucun UPDATE / DELETE / INSERT (aucun backfill, aucune donnée modifiée)")
check("consultation_allowance" not in V28_LOW and "earned_credits" not in V28_LOW
      and not re.search(r"\busers\b", V28_LOW) and "accounts" not in V28_LOW
      and "stripe" not in V28_LOW,
      "35 v28 ne touche AUCUNE autre table (que mobile_subscriptions)")
check(V28_LOW.count("alter table") == 1
      and "alter table mobile_subscriptions" in V28_LOW,
      "36 v28 : un seul ALTER TABLE, sur mobile_subscriptions")
check("current_period_start" not in V27_LOW,
      "37 v27 reste intacte : la colonne current_period_start est bien ajoutée par v28, pas par v27")

# --- Durcissement B2.3 : v28 est idempotente par IF NOT EXISTS SEUL, aucun
# try/except Python ne doit l'entourer (sinon une vraie erreur SQL serait avalée).
# NB : `\bexcept\b` ne matche PAS le mot SQL "exception" (pas de frontière t|i).
check(re.search(r"\bexcept\s+exception\b", V28_LOW) is None,
      "38 aucun `except Exception` général autour de v28")
check(re.search(r"\bexcept\b", V28_LOW) is None,
      "39 aucun try/except Python autour de v28 (idempotence 100 % SQL via IF NOT EXISTS)")
check("print(" not in V28_NOCOMMENT,
      "40 aucun print() d'erreur autour de v28 (une erreur non triviale n'est pas loggée puis ignorée)")
check("conn.rollback()" not in V28_NOCOMMENT,
      "41 aucun rollback masquant dans v28 : une vraie erreur SQL se propage")
check("try:" not in V28_NOCOMMENT
      and "c.execute(" in V28_NOCOMMENT
      and "conn.commit()" in V28_NOCOMMENT,
      "42 v28 = execute direct + commit, sans bloc try: (1er boot crée la colonne, boots suivants no-op)")

print("-" * 64)
print("H. Migration v29 — retour du quota Premium standard 10 -> 4 (règle produit finale)")

check(_m29.start() > _m28.start(),
      "43 v29 présente et placée APRÈS v28 dans init_db()")
# La requête peut être écrite en littéraux Python concaténés : on aplatit les
# guillemets et espaces pour retrouver le SQL logique.
V29_FLAT = re.sub(r"[\s\"']+", " ", V29_LOW)
check("alter table consultation_allowance alter column monthly_limit set default 4"
      in V29_FLAT,
      "44 v29 : ALTER TABLE consultation_allowance ... SET DEFAULT 4")
check("update consultation_allowance set monthly_limit = 4 where monthly_limit = 10"
      in V29_FLAT,
      "45 v29 : UPDATE ... SET monthly_limit = 4 WHERE monthly_limit = 10 (rabaisse le standard 10)")
check("monthly_used" not in V29_LOW,
      "46 v29 ne mentionne JAMAIS monthly_used (aucune remise à zéro des consommations)")
check("drop" not in V29_LOW and "delete " not in V29_LOW,
      "47 v29 : aucun DROP / DELETE (aucune période supprimée)")
check("earned_credits" not in V29_LOW and "consultations" not in V29_LOW
      and not re.search(r"\busers\b", V29_LOW) and "stripe" not in V29_LOW
      and "mobile_subscriptions" not in V29_LOW,
      "48 v29 ne touche que consultation_allowance (ni earned_credits, ni consultations, ni legacy, ni billing)")
check(V29_FLAT.count("where monthly_limit = 10") == 1,
      "49 v29 ne rabaisse QUE monthly_limit = 10 (les quotas custom != 10 restent intacts)")

# --- Durcissement Q1.1 : v29 est idempotente PAR NATURE (SET DEFAULT rejoué =
# même résultat ; UPDATE ... WHERE monthly_limit = 10 rejoué = 0 ligne). Aucun
# try/except Python ne doit l'entourer, sinon une vraie erreur SQL serait avalée.
# NB : `\bexcept\b` ne matche PAS le mot SQL "exception".
check(re.search(r"\bexcept\s+exception\b", V29_LOW) is None,
      "50 aucun `except Exception` général autour de v29")
check(re.search(r"\bexcept\b", V29_LOW) is None,
      "51 aucun try/except Python autour de v29 (une vraie erreur SQL se propage)")
check("print(" not in V29_NOCOMMENT,
      "52 aucun print() d'erreur autour de v29")
check("conn.rollback()" not in V29_NOCOMMENT,
      "53 aucun rollback masquant dans v29")
check("try:" not in V29_NOCOMMENT
      and V29_NOCOMMENT.count("c.execute(") == 2
      and "conn.commit()" in V29_NOCOMMENT,
      "54 v29 = 2 execute directs + commit, sans bloc try:")

# Non-régression : v25 (DEFAULT 4) et v26 (4 -> 10) NE SONT PAS réécrites.
check("monthly_limit INTEGER      NOT NULL DEFAULT 4" in SRC
      or re.search(r"monthly_limit\s+integer\s+not null\s+default 4", SRC.lower()) is not None,
      "55 v25 intacte : la DDL consultation_allowance garde DEFAULT 4")
check("ALTER TABLE consultation_allowance ALTER COLUMN monthly_limit SET DEFAULT 10" in SRC
      and "UPDATE consultation_allowance SET monthly_limit = 10 WHERE monthly_limit = 4" in SRC,
      "56 v26 intacte : ses deux instructions (SET DEFAULT 10 + UPDATE 4->10) sont toujours présentes, non réécrites")

print("-" * 64)
print("I. Migration v30 — première consultation OFFERTE (accounts.first_consultation_used_at)")

V30_FLAT = re.sub(r"[\s\"']+", " ", V30_LOW)
check(_m30.start() > _m29.start(),
      "57 v30 présente et placée APRÈS v29 dans init_db()")
check("alter table accounts add column if not exists first_consultation_used_at timestamptz"
      in V30_FLAT,
      "58 v30 : ALTER TABLE accounts ADD COLUMN IF NOT EXISTS first_consultation_used_at TIMESTAMPTZ")
check("if not exists" in V30_LOW,
      "59 ADD COLUMN IF NOT EXISTS -> migration rejouable sans erreur")
check("timestamptz" in V30_LOW,
      "60 v30 : colonne typée TIMESTAMPTZ")
check("update " not in V30_LOW and "insert " not in V30_LOW and "delete " not in V30_LOW,
      "61 v30 : AUCUN backfill (aucun UPDATE / INSERT / DELETE)")
check("drop" not in V30_LOW,
      "62 v30 : aucun DROP")
check(V30_LOW.count("alter table") == 1 and "alter table accounts" in V30_LOW
      and "consultation_allowance" not in V30_LOW and "consultations" not in V30_LOW
      and "earned_credits" not in V30_LOW and "mobile_subscriptions" not in V30_LOW,
      "63 v30 ne touche QUE accounts (un seul ALTER TABLE)")
check(re.search(r"\bexcept\b", V30_LOW) is None,
      "64 aucun try/except Python autour de v30")
check("print(" not in V30_NOCOMMENT and "conn.rollback()" not in V30_NOCOMMENT,
      "65 aucun print / rollback masquant dans v30")
check("try:" not in V30_NOCOMMENT and "c.execute(" in V30_NOCOMMENT
      and "conn.commit()" in V30_NOCOMMENT,
      "66 v30 = execute direct + commit, sans bloc try:")

# Non-régression : v25..v29 intactes.
check("Migration v25" in SRC and "Migration v27" in SRC and "Migration v28" in SRC
      and "Migration v29" in SRC,
      "67 v25 / v27 / v28 / v29 toujours présentes, non réécrites")
_SRC_FLAT = re.sub(r"[\s\"']+", " ", SRC.lower())
check("alter table consultation_allowance alter column monthly_limit set default 4"
      in _SRC_FLAT,
      "68 v29 (SET DEFAULT 4) toujours présente dans init_db(), non réécrite")

print("-" * 64)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
