"""
test_migration_time_v34.py — TIMER-A.1 : migration additive v34 (schéma du
futur compteur de temps en secondes).

Le harnais du projet ne joue jamais init_db() contre une vraie base (psycopg2
mocké). On prouve donc la migration au niveau du SOURCE de init_db() : forme du
DDL, additivité stricte, idempotence, non-régression sur les anciennes colonnes
(monthly_limit / monthly_used / expires_at). Style aligné sur
test_migration_v27.py (script, pas pytest).

En complément, une simulation Python PURE (sans SQL, sans mock DB) rejoue la
logique exacte du backfill first_free (CASE + garde WHERE ... IS NULL) pour
prouver le comportement 3600 / 0 et l'idempotence au rejeu.
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

# --- Isolation de la tranche v34 : du marqueur "# Migration v34" au marqueur de
# la migration suivante (# Migration v35, ajoutée en B7) si elle existe, sinon
# au conn.close() final de init_db(). v34 n'est plus la dernière migration.
_m34 = re.search(r"#\s*Migration v34\b", SRC)
assert _m34, "bloc 'Migration v34' introuvable dans init_db()"
_m33 = re.search(r"#\s*Migration v33\b", SRC)
assert _m33, "bloc 'Migration v33' introuvable dans init_db()"
assert _m34.start() > _m33.start(), "v34 doit venir APRES v33"

_tail = SRC[_m34.start():]
_m_next = re.search(r"#\s*Migration v3[5-9]\b", _tail)
if _m_next:
    V34 = _tail[:_m_next.start()]
else:
    _close = _tail.find("conn.close()")
    assert _close != -1, "conn.close() final introuvable apres le bloc v34"
    V34 = _tail[:_close + len("conn.close()")]


def _nocomment(block):
    return "\n".join(
        line for line in block.splitlines() if not line.lstrip().startswith("#")
    )


V34_NC = _nocomment(V34)
V34_LOW = V34_NC.lower()
V34_FLAT = re.sub(r"[\s\"']+", " ", V34_LOW)

print("-" * 64)
print("A. Ordre & presence dans init_db()")

check("Migration v33" in SRC and SRC.index("Migration v33") < _m34.start(),
      "1 v34 ajoutee APRES la migration v33 existante")
check("Migration v30" in SRC and "Migration v32" in SRC,
      "2 les migrations anterieures (v30, v32) sont toujours presentes")

print("-" * 64)
print("B. accounts.first_free_seconds_remaining — ADD COLUMN SANS default + backfill CASE")

check("alter table accounts add column if not exists first_free_seconds_remaining integer"
      in V34_FLAT,
      "3 ADD COLUMN IF NOT EXISTS accounts.first_free_seconds_remaining INTEGER")
check(re.search(
        r"add column if not exists first_free_seconds_remaining integer\s+default",
        V34_FLAT) is None,
      "4 first_free_seconds_remaining ajoutee SANS DEFAULT (le CASE doit voir les NULL)")
check(re.search(
        r"update accounts set first_free_seconds_remaining = "
        r"case when first_consultation_used_at is null then 3600 else 0 end "
        r"where first_free_seconds_remaining is null",
        V34_FLAT) is not None,
      "5 backfill : CASE fcua IS NULL -> 3600 / sinon 0, garde WHERE ffsr IS NULL")

print("-" * 64)
print("C. accounts.purchased_seconds_remaining — DEFAULT 0")

check("alter table accounts add column if not exists purchased_seconds_remaining integer default 0"
      in V34_FLAT,
      "6 ADD COLUMN IF NOT EXISTS accounts.purchased_seconds_remaining INTEGER DEFAULT 0")

print("-" * 64)
print("D. consultation_allowance — monthly_allowance_seconds 28800 / monthly_used_seconds 0")

check("alter table consultation_allowance add column if not exists monthly_allowance_seconds integer default 28800"
      in V34_FLAT,
      "7 ADD COLUMN IF NOT EXISTS monthly_allowance_seconds INTEGER DEFAULT 28800")
check("alter table consultation_allowance add column if not exists monthly_used_seconds integer default 0"
      in V34_FLAT,
      "8 ADD COLUMN IF NOT EXISTS monthly_used_seconds INTEGER DEFAULT 0")

print("-" * 64)
print("E. consultations — last_activity_at / billed_until TIMESTAMPTZ NULL")

check("alter table consultations add column if not exists last_activity_at timestamptz"
      in V34_FLAT,
      "9 ADD COLUMN IF NOT EXISTS consultations.last_activity_at TIMESTAMPTZ")
check("alter table consultations add column if not exists billed_until timestamptz"
      in V34_FLAT,
      "10 ADD COLUMN IF NOT EXISTS consultations.billed_until TIMESTAMPTZ")
check(re.search(r"last_activity_at timestamptz\s+(default|not null)", V34_FLAT) is None
      and re.search(r"billed_until timestamptz\s+(default|not null)", V34_FLAT) is None,
      "11 last_activity_at / billed_until : ni DEFAULT ni NOT NULL -> NULL par defaut")

print("-" * 64)
print("F. Idempotence")

check(V34_FLAT.count("add column if not exists") == 6,
      "12 les 6 ADD COLUMN portent TOUS IF NOT EXISTS (rejeu = no-op)")
check("where first_free_seconds_remaining is null" in V34_FLAT,
      "13 le backfill est garde par WHERE ffsr IS NULL (rejeu -> 0 ligne, valeurs posees preservees)")
check(re.search(r"\bexcept\b", V34_LOW) is None
      and "conn.rollback()" not in V34_NC
      and "print(" not in V34_NC,
      "14 aucun try/except Python / rollback / print (une vraie erreur SQL remonte, cf. v28..v33)")
check(V34_NC.count("conn.commit()") == 1,
      "15 un seul conn.commit() apres les 8 statements v34 (7 DDL + 1 backfill)")

print("-" * 64)
print("G. Additivite stricte — anciennes colonnes intactes, rien supprime")

check("drop" not in V34_LOW, "16 aucun DROP dans la tranche v34")
check("truncate" not in V34_LOW, "17 aucun TRUNCATE dans la tranche v34")
check("delete" not in V34_LOW, "18 aucun DELETE dans la tranche v34")
check(re.search(r"\bmonthly_limit\b", V34_LOW) is None,
      "19 v34 ne reference JAMAIS monthly_limit (semantique par-compte intacte)")
check(re.search(r"\bmonthly_used\b", V34_LOW) is None,
      "20 v34 ne reference JAMAIS monthly_used seul (distinct de monthly_used_seconds)")
check(re.search(r"\bexpires_at\b", V34_LOW) is None,
      "21 v34 ne touche JAMAIS consultations.expires_at (cutoff 2 h encore actif)")
check("first_consultation_used_at" in V34_LOW
      and "drop column" not in V34_LOW,
      "22 first_consultation_used_at LUE par le CASE, jamais supprimee")
# Le SEUL `ALTER COLUMN` autorise : SET DEFAULT sur la colonne que CE bloc vient
# d'ajouter. Interdits : ALTER COLUMN sur une colonne preexistante, ou TYPE /
# DROP DEFAULT / SET NOT NULL sur quoi que ce soit.
check(V34_FLAT.count("alter column") == 1,
      "23 un seul ALTER COLUMN dans toute la tranche v34")
_alter_cols = re.findall(
    r"alter table (\w+) alter column (\w+) set default (\d+)", V34_FLAT)
check(_alter_cols == [("accounts", "first_free_seconds_remaining", "3600")],
      f"23a l'unique ALTER COLUMN = accounts.first_free_seconds_remaining SET DEFAULT 3600 "
      f"(trouve : {_alter_cols})")
check(re.search(r"alter column \w+ (type |drop default|set not null)", V34_FLAT) is None,
      "23b aucun ALTER COLUMN ... TYPE / DROP DEFAULT / SET NOT NULL")
for _old in ("monthly_limit", "monthly_used", "expires_at", "first_consultation_used_at"):
    check(re.search(r"alter column " + _old + r"\b", V34_FLAT) is None,
          f"23c aucune colonne preexistante ALTER-ee : {_old}")

print("-" * 64)
print("D2. DEFAULT first_free_seconds_remaining = 3600, pose APRES le backfill")

check("alter table accounts alter column first_free_seconds_remaining set default 3600"
      in V34_FLAT,
      "23d ALTER TABLE accounts ALTER COLUMN first_free_seconds_remaining SET DEFAULT 3600")
_i_backfill = V34_FLAT.find("update accounts set first_free_seconds_remaining")
_i_default = V34_FLAT.find(
    "alter table accounts alter column first_free_seconds_remaining set default 3600")
check(_i_backfill != -1 and _i_default != -1 and _i_default > _i_backfill,
      "23e le SET DEFAULT est place APRES l'UPDATE de backfill "
      "(ne transforme pas les comptes deja consommes en 3600)")

print("-" * 64)
print("H. Modele par-compte : aucune fonction NE LIT/N'ECRIT en CODE les 6 colonnes")

import ast as _ast
import textwrap as _tw

_NEW_COLS = [
    "first_free_seconds_remaining", "purchased_seconds_remaining",
    "monthly_allowance_seconds", "monthly_used_seconds",
    "last_activity_at", "billed_until",
]


def _code_only(fn):
    """Source de `fn` SANS docstring ni commentaires (ast.unparse) : on teste le
    CODE, pas la doc. TIMER-A.3b documente monthly_*_seconds dans le docstring de
    _insert_allowance sans les écrire (DEFAULT de schéma v34)."""
    _t = _ast.parse(_tw.dedent(inspect.getsource(fn)))
    _f = _t.body[0]
    if (_f.body and isinstance(_f.body[0], _ast.Expr)
            and isinstance(getattr(_f.body[0], "value", None), _ast.Constant)
            and isinstance(_f.body[0].value.value, str)):
        _f.body = _f.body[1:]
    return _ast.unparse(_f)


for _fn_name in ("open_or_get_consultation", "get_consultation_state",
                 "api_consultation_message", "api_consultation_state",
                 "resync_premium_entitlement", "_resync_premium_entitlement_tx",
                 "_insert_allowance", "provision_allowance", "grant_earned_credit"):
    _fn = getattr(A, _fn_name, None)
    if _fn is None:
        check(False, f"24 fonction {_fn_name} introuvable")
        continue
    _hits = [col for col in _NEW_COLS if col in _code_only(_fn)]
    check(not _hits, f"24 {_fn_name} n'utilise AUCUNE nouvelle colonne temps EN CODE"
          + (f" (trouve : {_hits})" if _hits else ""))

print("-" * 64)
print("I. Simulation PURE de la logique du backfill (pas de SQL, pas de mock DB)")


def _apply_v34_first_free_backfill(rows):
    """Rejoue EXACTEMENT :
       UPDATE accounts
         SET first_free_seconds_remaining =
             CASE WHEN first_consultation_used_at IS NULL THEN 3600 ELSE 0 END
         WHERE first_free_seconds_remaining IS NULL
    Retourne le nombre de lignes modifiees (comme rowcount)."""
    touched = 0
    for r in rows:
        if r["first_free_seconds_remaining"] is None:          # WHERE ... IS NULL
            r["first_free_seconds_remaining"] = (
                3600 if r["first_consultation_used_at"] is None else 0
            )
            touched += 1
    return touched


# A. nouveau compte jamais utilise -> 3600
_new = {"first_consultation_used_at": None, "first_free_seconds_remaining": None}
# B. compte gratuite deja consommee -> 0
_used = {"first_consultation_used_at": "2026-08-01T10:00:00Z",
         "first_free_seconds_remaining": None}
# valeur deja posee par un futur moteur -> ne doit PAS bouger
_engine = {"first_consultation_used_at": None, "first_free_seconds_remaining": 1200}

_rows = [_new, _used, _engine]
_n1 = _apply_v34_first_free_backfill(_rows)

check(_new["first_free_seconds_remaining"] == 3600,
      "A compte existant jamais utilise (fcua IS NULL) -> first_free_seconds_remaining = 3600")
check(_used["first_free_seconds_remaining"] == 0,
      "B compte existant deja utilise (fcua renseignee) -> 0")
check(_engine["first_free_seconds_remaining"] == 1200,
      "C valeur preexistante custom (1200) NON ecrasee par le backfill")
check(_n1 == 2, "C1 1er passage : exactement 2 lignes NULL regularisees")

# F. rejeu : aucune erreur, aucune double modification, valeurs posees intactes
_n2 = _apply_v34_first_free_backfill(_rows)
check(_n2 == 0, "F 2e passage (rejeu) : 0 ligne modifiee")
check(_new["first_free_seconds_remaining"] == 3600
      and _used["first_free_seconds_remaining"] == 0
      and _engine["first_free_seconds_remaining"] == 1200,
      "F1 2e passage : toutes les valeurs (3600 / 0 / 1200 custom) inchangees")

# E. NOUVEAU compte cree APRES la migration : l'INSERT INTO accounts existant
# n'enumere pas first_free_seconds_remaining -> la valeur vient du DEFAULT de
# colonne (3600), pose par l'ALTER COLUMN ... SET DEFAULT de v34.
_COL_DEFAULT = int(re.search(
    r"alter table accounts alter column first_free_seconds_remaining set default (\d+)",
    V34_FLAT).group(1))


def _insert_account_post_migration(explicit_first_free=None):
    """Rejoue un INSERT INTO accounts (...) qui N'enumere PAS
    first_free_seconds_remaining : la colonne prend son DEFAULT."""
    return {"first_consultation_used_at": None,
            "first_free_seconds_remaining":
                _COL_DEFAULT if explicit_first_free is None else explicit_first_free}


_fresh = _insert_account_post_migration()
check(_COL_DEFAULT == 3600, "D DEFAULT de first_free_seconds_remaining = 3600")
check(_fresh["first_free_seconds_remaining"] == 3600,
      "E nouveau compte cree APRES la migration (colonne non enumeree) -> 3600 via le DEFAULT")
# le backfill re-passe : la ligne n'est plus NULL -> intouchee
check(_apply_v34_first_free_backfill([_fresh]) == 0
      and _fresh["first_free_seconds_remaining"] == 3600,
      "E2 le backfill ne retouche pas un nouveau compte deja a 3600 (WHERE ... IS NULL)")

# Defauts des autres colonnes a valeur constante (lecture directe du DDL)
check("purchased_seconds_remaining integer default 0" in V34_FLAT,
      "C2 purchased_seconds_remaining : DEFAULT 0 (donnees existantes = 0)")
check("monthly_allowance_seconds integer default 28800" in V34_FLAT,
      "C3 monthly_allowance_seconds : DEFAULT 28800 (lignes existantes lisent 28800)")
check("monthly_used_seconds integer default 0" in V34_FLAT,
      "C4 monthly_used_seconds : DEFAULT 0")

# last_activity_at / billed_until sans valeur -> NULL
check(re.search(r"last_activity_at timestamptz(?!\s+(default|not null))", V34_FLAT)
      is not None,
      "C5 last_activity_at -> NULL (aucune valeur imposee)")
check(re.search(r"billed_until timestamptz(?!\s+(default|not null))", V34_FLAT)
      is not None,
      "C6 billed_until -> NULL (aucune valeur imposee)")

print("-" * 64)
print("J. Miroir migrations/008_time_seconds.sql coherent avec init_db()")

try:
    with open(os.path.join(os.path.dirname(__file__), "migrations",
                           "008_time_seconds.sql"), encoding="utf-8") as _f:
        _MIRROR_RAW = _f.read().lower()
    # Retire les lignes de commentaire SQL (`-- ...`) : la prose d'en-tete cite
    # volontairement « DROP / TRUNCATE / DELETE » pour affirmer leur absence.
    _MIRROR = "\n".join(
        ln for ln in _MIRROR_RAW.splitlines() if not ln.lstrip().startswith("--")
    )
    _MIRROR_FLAT = re.sub(r"[\s\"']+", " ", _MIRROR)
    for _needle, _lbl in [
        ("add column if not exists first_free_seconds_remaining integer",
         "first_free_seconds_remaining"),
        ("add column if not exists purchased_seconds_remaining integer default 0",
         "purchased_seconds_remaining DEFAULT 0"),
        ("case when first_consultation_used_at is null then 3600 else 0 end",
         "backfill CASE 3600/0"),
        ("where first_free_seconds_remaining is null", "garde WHERE ffsr IS NULL"),
        ("alter table accounts alter column first_free_seconds_remaining set default 3600",
         "SET DEFAULT 3600 sur first_free_seconds_remaining"),
        ("add column if not exists monthly_allowance_seconds integer default 28800",
         "monthly_allowance_seconds DEFAULT 28800"),
        ("add column if not exists monthly_used_seconds integer default 0",
         "monthly_used_seconds DEFAULT 0"),
        ("add column if not exists last_activity_at timestamptz", "last_activity_at"),
        ("add column if not exists billed_until timestamptz", "billed_until"),
    ]:
        check(_needle in _MIRROR_FLAT, f"25 miroir 008 contient : {_lbl}")
    check("drop" not in _MIRROR and "truncate" not in _MIRROR
          and "delete" not in _MIRROR,
          "26 miroir 008 : aucun DROP / TRUNCATE / DELETE")
    _mi_bf = _MIRROR_FLAT.find("update accounts set first_free_seconds_remaining")
    _mi_def = _MIRROR_FLAT.find(
        "alter table accounts alter column first_free_seconds_remaining set default 3600")
    check(_mi_bf != -1 and _mi_def != -1 and _mi_def > _mi_bf,
          "27 miroir 008 : SET DEFAULT place APRES l'UPDATE de backfill")
    check(_MIRROR_FLAT.count("alter column") == 1,
          "28 miroir 008 : un seul ALTER COLUMN (le SET DEFAULT)")
except FileNotFoundError:
    check(False, "25 migrations/008_time_seconds.sql introuvable")

print("-" * 64)
print(f"RESULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
