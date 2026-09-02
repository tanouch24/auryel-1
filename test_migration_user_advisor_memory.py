"""
test_migration_user_advisor_memory.py — B7 : migration additive v35
(table `user_advisor_memory`, mémoire inter-session par conseiller).

Deux niveaux :

  PARTIE 1 (toujours) — preuve au niveau du SOURCE de init_db() + du miroir
  migrations/009_user_advisor_memory.sql : forme du DDL, additivité stricte,
  idempotence, UNIQUE(user_id, advisor_id), FK idempotente, ABSENCE de
  facts_json, non-régression (aucun DROP / ALTER d'une autre table dans la
  tranche v35). Le harnais du projet ne joue jamais init_db() contre une vraie
  base (psycopg2 mocké) — même parti pris que test_migration_time_v34.py.

  PARTIE 2 (si A4_PG_DSN défini) — application RÉELLE sur un PostgreSQL LOCAL /
  DE TEST : init_db() exécuté, puis vérification table / types / NOT NULL /
  DEFAULT / UNIQUE / FK / idempotence (rejeu init_db) / insert / update /
  isolation (user_id, advisor_id). Garde de sécurité identique à
  test_time_postgres_integration.py : host local obligatoire, nom de base
  "test"/"a4", rejet des marqueurs d'hébergeur managé, aucun DROP de base,
  nettoyage des seules lignes de test. SKIP propre si A4_PG_DSN absent.

Usage PARTIE 2 :
    A4_PG_DSN="postgresql://a4test:...@127.0.0.1:5432/auryel_test" \
        python3 test_migration_user_advisor_memory.py
"""

import os
import re
import sys

_STATE = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _STATE["pass"] += 1
        print(f"✅ {label}")
    else:
        _STATE["fail"] += 1
        print(f"❌ {label}")


# ===========================================================================
# PARTIE 1 — SOURCE de init_db() + miroir 009
# ===========================================================================
_HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(_HERE, "auryel_bot.py"), encoding="utf-8").read()
MIRROR = open(os.path.join(_HERE, "migrations", "009_user_advisor_memory.sql"),
              encoding="utf-8").read()

_m35 = re.search(r"#\s*Migration v35\b", SRC)
_m34 = re.search(r"#\s*Migration v34\b", SRC)
assert _m35, "bloc 'Migration v35' introuvable dans init_db()"
assert _m34, "bloc 'Migration v34' introuvable dans init_db()"

_tail = SRC[_m35.start():]
_close = _tail.find("conn.close()")
assert _close != -1, "conn.close() final introuvable apres le bloc v35"
V35 = _tail[:_close + len("conn.close()")]


def _nocomment(block):
    return "\n".join(l for l in block.splitlines() if not l.lstrip().startswith("#"))


V35_NC = _nocomment(V35)
V35_FLAT = re.sub(r"[\s\"']+", " ", V35_NC.lower())
MIRROR_NC = "\n".join(l for l in MIRROR.splitlines() if not l.lstrip().startswith("--"))
MIRROR_FLAT = re.sub(r"[\s]+", " ", MIRROR_NC.lower())

print("-" * 64)
print("A. Ordre & présence")
check(_m35.start() > _m34.start(), "1 v35 vient APRÈS v34 dans init_db()")
check("create table if not exists user_advisor_memory" in V35_FLAT,
      "2 CREATE TABLE IF NOT EXISTS user_advisor_memory (idempotent)")
check("create table if not exists user_advisor_memory" in MIRROR_FLAT,
      "3 miroir 009 : même CREATE TABLE IF NOT EXISTS")

print("-" * 64)
print("B. Colonnes exactes")
for frag, lbl in [
    ("id uuid primary key", "id UUID PRIMARY KEY"),
    ("user_id uuid not null", "user_id UUID NOT NULL"),
    ("advisor_id text not null", "advisor_id TEXT NOT NULL"),
    ("summary text not null default ", "summary TEXT NOT NULL DEFAULT ''"),
    ("last_source_message_id bigint not null default 0", "last_source_message_id BIGINT NOT NULL DEFAULT 0"),
    ("created_at timestamptz not null", "created_at TIMESTAMPTZ NOT NULL"),
    ("updated_at timestamptz not null", "updated_at TIMESTAMPTZ NOT NULL"),
]:
    check(frag in V35_FLAT, f"4 colonne {lbl}")
    check(frag in MIRROR_FLAT, f"4' miroir : colonne {lbl}")

print("-" * 64)
print("C. Clé unique")
check("unique (user_id, advisor_id)" in V35_FLAT, "5 UNIQUE (user_id, advisor_id) dans init_db()")
check("unique (user_id, advisor_id)" in MIRROR_FLAT, "5' UNIQUE (user_id, advisor_id) dans le miroir")

print("-" * 64)
print("D. FK idempotente vers accounts(user_id) AVEC ON DELETE CASCADE (B7.1)")
check("foreign key (user_id) references accounts(user_id)" in V35_FLAT,
      "6 FK user_advisor_memory.user_id -> accounts(user_id)")
check("do $$" in V35_FLAT and "duplicate_object" in V35_FLAT,
      "7 FK posée dans DO $$ ... EXCEPTION WHEN duplicate_object (idempotent, style v33)")
check("foreign key (user_id) references accounts(user_id) on delete cascade" in V35_FLAT,
      "8 FK EN ON DELETE CASCADE (B7.1 : mémoire = donnée perso, disparaît avec le compte)")
check("foreign key (user_id) references accounts(user_id) on delete cascade" in MIRROR_FLAT
      and "duplicate_object" in MIRROR_FLAT,
      "8' miroir : même FK idempotente ON DELETE CASCADE")

print("-" * 64)
print("E. Additivité stricte de la tranche v35")
check("drop table" not in V35_FLAT and "drop column" not in V35_FLAT,
      "9 aucun DROP dans la tranche v35")
check("truncate" not in V35_FLAT and "delete from" not in V35_FLAT,
      "10 aucun TRUNCATE / DELETE")
check("alter table users" not in V35_FLAT and "alter table messages" not in V35_FLAT
      and "alter table consultations" not in V35_FLAT
      and "alter table app_profiles" not in V35_FLAT
      and "alter table accounts" not in V35_FLAT,
      "11 aucune autre table modifiée (seul ALTER = la FK sur user_advisor_memory)")
check(V35_FLAT.count("alter table user_advisor_memory") >= 1
      and "alter table user_advisor_memory add constraint" in V35_FLAT,
      "12 le seul ALTER TABLE porte sur user_advisor_memory (ADD CONSTRAINT FK)")
check("update " not in V35_FLAT.replace("do update set", ""),
      "13 aucun backfill / UPDATE de données (hors ON CONFLICT ... DO UPDATE applicatif)")

print("-" * 64)
print("F. facts_json VOLONTAIREMENT absent (décision B7 §2 / §3)")
check("facts_json" not in V35_FLAT, "14 pas de colonne facts_json dans le DDL v35 (hors commentaires)")
check("facts_json" not in MIRROR_FLAT, "14' pas de colonne facts_json dans le DDL du miroir 009")

print("-" * 64)
print("G. Idempotence globale de la tranche")
check(V35_FLAT.count("create table") == 1
      and "create table if not exists user_advisor_memory" in V35_FLAT,
      "15 un seul CREATE TABLE, toujours gardé par IF NOT EXISTS")
check("exception when duplicate_object then null" in V35_FLAT,
      "16 FK : EXCEPTION WHEN duplicate_object THEN NULL (rejeu sans erreur)")

# ===========================================================================
# PARTIE 2 — PostgreSQL RÉEL (opt-in via A4_PG_DSN)
# ===========================================================================
_DSN = os.environ.get("A4_PG_DSN")
print("-" * 64)
if not _DSN:
    print("PARTIE 2 (PostgreSQL réel) : SKIP — A4_PG_DSN non défini.")
    print("  Fournir un DSN PostgreSQL LOCAL / DE TEST (jamais la prod) pour l'exécuter.")
    print("-" * 64)
    print(f"RÉSULTAT PARTIE 1 : {_STATE['pass']} ok / {_STATE['fail']} ko")
    sys.exit(1 if _STATE["fail"] else 0)


def _dsn_is_local_test(dsn):
    low = dsn.lower()
    for bad in ("railway", "rlwy", "proxy.rlwy", "amazonaws", "supabase", "neon.tech",
                "herokupg", "render.com", "azure", "gcp"):
        if bad in low:
            return False, "marqueur d'hébergeur managé"
    if not re.search(r"@(localhost|127\.0\.0\.1|\[::1\]|::1)[:/]", low):
        return False, "host non local"
    if "test" not in low and "a4" not in low and "b7" not in low:
        return False, "nom de base sans 'test' / 'a4' / 'b7'"
    return True, ""


_ok, _why = _dsn_is_local_test(_DSN)
if not _ok:
    print(f"REFUS : A4_PG_DSN ne ressemble pas à une base locale/de test — {_why}.")
    print("        Aucune connexion effectuée. (DSN jamais affiché.)")
    sys.exit(2)

# psycopg2 réel requis ici — retirer le mock éventuel.
sys.modules.pop("psycopg2", None)
import psycopg2  # noqa: E402

os.environ["DATABASE_URL"] = _DSN
for _k, _v in {
    "SECRET_KEY": "t", "VERIFY_TOKEN": "t", "ADMIN_PASSWORD": "t", "CRON_SECRET": "t",
    "STRIPE_SK": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x", "WHATSAPP_TOKEN": "t",
    "PHONE_NUMBER_ID": "t", "GROQ_API_KEY": "t", "RESEND_API_KEY": "t",
    "META_APP_SECRET": "t", "DAILY_SECRET": "t", "SEO_SECRET": "t",
    "TAROT_MEDIA_UPLOAD_DISABLED": "1",
}.items():
    os.environ.setdefault(_k, _v)

import uuid as _uuid  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import auryel_bot as A  # noqa: E402

print("PARTIE 2 (PostgreSQL réel) : init_db() en cours…")
A.init_db()
A.init_db()   # 2e passage : doit être un no-op (idempotence)
print("  init_db() rejoué 2x sans erreur.")

conn = psycopg2.connect(_DSN)
conn.autocommit = True
cur = conn.cursor()

# --- structure ---
cur.execute("""
    SELECT column_name, data_type, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_name = 'user_advisor_memory'
    ORDER BY ordinal_position
""")
cols = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
check(set(cols) == {"id", "user_id", "advisor_id", "summary",
                    "last_source_message_id", "created_at", "updated_at"},
      f"P2-1 colonnes exactes : {sorted(cols)}")
check(cols.get("id", (None,))[0] == "uuid", "P2-2 id = uuid")
check(cols.get("user_id", (None,))[0] == "uuid"
      and cols["user_id"][1] == "NO", "P2-3 user_id uuid NOT NULL")
check(cols.get("advisor_id", (None,))[0] == "text"
      and cols["advisor_id"][1] == "NO", "P2-4 advisor_id text NOT NULL")
check(cols.get("summary", (None,))[1] == "NO"
      and "''" in (cols["summary"][2] or ""), "P2-5 summary NOT NULL DEFAULT ''")
check(cols.get("last_source_message_id", (None,))[0] == "bigint"
      and cols["last_source_message_id"][1] == "NO"
      and (cols["last_source_message_id"][2] or "").startswith("0"),
      "P2-6 last_source_message_id bigint NOT NULL DEFAULT 0")
check(cols.get("created_at", (None,))[0] == "timestamp with time zone"
      and cols.get("updated_at", (None,))[0] == "timestamp with time zone",
      "P2-7 created_at / updated_at TIMESTAMPTZ")

# --- UNIQUE ---
cur.execute("""
    SELECT COUNT(*) FROM pg_constraint
    WHERE conrelid = 'user_advisor_memory'::regclass AND contype = 'u'
""")
check(cur.fetchone()[0] >= 1, "P2-8 contrainte UNIQUE présente")

# --- FK ---
cur.execute("""
    SELECT confrelid::regclass::text, confdeltype FROM pg_constraint
    WHERE conrelid = 'user_advisor_memory'::regclass AND contype = 'f'
""")
fks = cur.fetchall()
check(any(f[0] == "accounts" for f in fks), "P2-9 FK -> accounts")
check(len(fks) == 1 and fks[0][1] == "c",
      "P2-10 FK ON DELETE CASCADE (pg_constraint.confdeltype = 'c')  [B7.1]")

# --- insert / update / isolation ---
u1 = str(_uuid.uuid4())
u2 = str(_uuid.uuid4())
now = datetime.now(timezone.utc)
for u in (u1, u2):
    cur.execute("INSERT INTO accounts (user_id, email, created_at) VALUES (%s,%s,%s) "
                "ON CONFLICT (user_id) DO NOTHING", (u, f"{u}@t.test", now))

try:
    A.maybe_refresh_advisor_memory  # sanity : fonction exportée
    check(True, "P2-11 maybe_refresh_advisor_memory importable")
except Exception:
    check(False, "P2-11 maybe_refresh_advisor_memory importable")

cur.execute("INSERT INTO user_advisor_memory (id,user_id,advisor_id,summary,"
            "last_source_message_id,created_at,updated_at) VALUES (%s,%s,'thea','S1',5,%s,%s)",
            (str(_uuid.uuid4()), u1, now, now))
cur.execute("INSERT INTO user_advisor_memory (id,user_id,advisor_id,summary,"
            "last_source_message_id,created_at,updated_at) VALUES (%s,%s,'selena','S2',0,%s,%s)",
            (str(_uuid.uuid4()), u1, now, now))
cur.execute("INSERT INTO user_advisor_memory (id,user_id,advisor_id,summary,"
            "last_source_message_id,created_at,updated_at) VALUES (%s,%s,'thea','AUTRE',9,%s,%s)",
            (str(_uuid.uuid4()), u2, now, now))
check(True, "P2-12 3 insertions (2 advisors pour u1, 1 pour u2) OK")

dup_ok = False
try:
    cur.execute("INSERT INTO user_advisor_memory (id,user_id,advisor_id,summary,"
                "last_source_message_id,created_at,updated_at) VALUES (%s,%s,'thea','DUP',1,%s,%s)",
                (str(_uuid.uuid4()), u1, now, now))
except psycopg2.errors.UniqueViolation:
    dup_ok = True
check(dup_ok, "P2-13 2e (u1,'thea') rejeté par UNIQUE(user_id, advisor_id)")

cur.execute("UPDATE user_advisor_memory SET summary='S1b', last_source_message_id=12 "
            "WHERE user_id=%s AND advisor_id='thea'", (u1,))
cur.execute("SELECT summary, last_source_message_id FROM user_advisor_memory "
            "WHERE user_id=%s AND advisor_id='thea'", (u1,))
check(cur.fetchone() == ("S1b", 12), "P2-14 UPDATE mémoire (u1,'thea') OK")

cur.execute("SELECT summary FROM user_advisor_memory WHERE user_id=%s AND advisor_id='selena'", (u1,))
check(cur.fetchone() == ("S2",), "P2-15 (u1,'selena') NON affecté par l'update 'thea' (isolation advisor)")
cur.execute("SELECT summary FROM user_advisor_memory WHERE user_id=%s AND advisor_id='thea'", (u2,))
check(cur.fetchone() == ("AUTRE",), "P2-16 (u2,'thea') NON affecté (isolation user)")

# --- B7.1 : suppression de compte -> mémoire supprimée en cascade ---
# u1 a 2 lignes mémoire ('thea', 'selena'), u2 en a 1 ('thea').
cur.execute("SELECT COUNT(*) FROM user_advisor_memory WHERE user_id=%s", (u1,))
check(cur.fetchone()[0] == 2, "P2-17 état initial : 2 lignes mémoire pour u1")

# Aucune FK RESTRICT ne doit bloquer le DELETE de u1 (u1 n'a QUE des lignes
# user_advisor_memory ici -> la seule FK en jeu est celle qu'on teste).
del_ok = True
try:
    cur.execute("DELETE FROM accounts WHERE user_id=%s", (u1,))
except Exception as e:
    del_ok = False
    print(f"   (DELETE accounts u1 a échoué : {type(e).__name__})")
check(del_ok, "P2-18 DELETE FROM accounts WHERE user_id=u1 réussit (FK non bloquante)")

cur.execute("SELECT COUNT(*) FROM user_advisor_memory WHERE user_id=%s", (u1,))
check(cur.fetchone()[0] == 0, "P2-19 les 2 lignes mémoire de u1 supprimées EN CASCADE (0 orpheline)")

cur.execute("SELECT COUNT(*) FROM accounts WHERE user_id=%s", (u2,))
check(cur.fetchone()[0] == 1, "P2-20 compte u2 intact")
cur.execute("SELECT summary FROM user_advisor_memory WHERE user_id=%s AND advisor_id='thea'", (u2,))
check(cur.fetchone() == ("AUTRE",), "P2-21 mémoire de u2 intacte (isolation user à la cascade)")

# idempotence : rejouer init_db() une 3e fois APRÈS des données -> no-op
A.init_db()
cur.execute("SELECT COUNT(*) FROM user_advisor_memory WHERE user_id=%s", (u2,))
check(cur.fetchone()[0] == 1, "P2-22 init_db() rejoué après données : mémoire u2 toujours là (idempotent)")

# --- nettoyage : SEULES nos lignes ---
cur.execute("DELETE FROM user_advisor_memory WHERE user_id IN (%s,%s)", (u1, u2))
cur.execute("DELETE FROM accounts WHERE user_id IN (%s,%s)", (u1, u2))
cur.close()
conn.close()

print("-" * 64)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
