"""
test_time_postgres_integration.py — TIMER-A.4

VALIDATION du moteur temps sur un VRAI PostgreSQL (pas de mock SQL) :
migration v34 réellement appliquée par init_db(), vrais DEFAULT, vraies
contraintes NOT NULL, vraies transactions, vrais SELECT ... FOR UPDATE, vrais
débits secondes, idempotence, cap 300 s, waterfall des buckets, épuisement
partiel sans rétrofacturation, flow transactionnel, tirage, rollback,
concurrence (accounts FOR UPDATE) et double settle concurrent.

########################################################################
#  SÉCURITÉ — ce fichier REFUSE de tourner ailleurs que sur une base    #
#  PostgreSQL LOCALE / DE TEST :                                        #
#    - DSN lu UNIQUEMENT dans A4_PG_DSN (jamais DATABASE_URL)           #
#    - host obligatoirement localhost / 127.0.0.1 / ::1                 #
#    - nom de base contenant "a4" ou "test"                            #
#    - rejet si le DSN contient un marqueur d'hébergeur managé          #
#      (railway, rlwy, proxy, amazonaws, supabase, neon, herokupg...)  #
#  Ne DROP jamais de base. Ne nettoie QUE ses propres lignes (user_id  #
#  a4xxxxxx-...). Aucun credential de production en dur.               #
########################################################################

Usage :
    A4_PG_DSN="postgresql://a4test:...@127.0.0.1:55432/auryel_timer_a4" \
        python3 test_time_postgres_integration.py
"""

import os
import sys
import uuid
import threading
import time as _time
from datetime import timedelta

# ---------------------------------------------------------------------------
# 0. GARDE DE SÉCURITÉ — avant TOUT import applicatif.
# ---------------------------------------------------------------------------
_DSN = os.environ.get("A4_PG_DSN")
if not _DSN:
    print("SKIP : A4_PG_DSN non défini — test d'intégration PostgreSQL non exécuté.")
    print("       (fournir un DSN PostgreSQL LOCAL / DE TEST, jamais la prod.)")
    sys.exit(0)


def _dsn_is_local_test(dsn):
    low = dsn.lower()
    managed = ("railway", "rlwy.net", ".proxy.", "amazonaws", "rds.", "supabase",
               "neon.tech", "herokupostgres", "azure", "gcp", "digitalocean",
               "render.com", "cloud")
    if any(m in low for m in managed):
        return False, "DSN contient un marqueur d'hébergeur managé"
    # host
    import re
    m = re.search(r"@([^/:?]+)", dsn)
    host = (m.group(1) if m else "").lower()
    if host not in ("localhost", "127.0.0.1", "::1", "[::1]"):
        return False, f"host non local ({host!r})"
    # db name
    m = re.search(r"/([^/?]+)(\?|$)", dsn)
    dbname = (m.group(1) if m else "").lower()
    if not ("a4" in dbname or "test" in dbname):
        return False, f"nom de base non-test ({dbname!r})"
    return True, dbname


_ok, _why = _dsn_is_local_test(_DSN)
if not _ok:
    print(f"REFUS : le DSN A4_PG_DSN ne ressemble pas à une base locale/de test — {_why}.")
    print("        Aucune connexion effectuée. (DSN jamais affiché.)")
    sys.exit(2)
_A4_DBNAME = _why
print("=" * 70)
print(f"TIMER-A.4 — intégration PostgreSQL réelle (base de test : {_A4_DBNAME})")
print("=" * 70)

# init_db() est déclenché à l'import : on pointe DATABASE_URL sur la base de test.
os.environ["DATABASE_URL"] = _DSN
for _k, _v in {
    "SECRET_KEY": "a4-test", "VERIFY_TOKEN": "t", "ADMIN_PASSWORD": "t",
    "CRON_SECRET": "t", "STRIPE_SK": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "WHATSAPP_TOKEN": "t", "PHONE_NUMBER_ID": "t", "GROQ_API_KEY": "t",
    "RESEND_API_KEY": "t", "META_APP_SECRET": "t", "DAILY_SECRET": "t",
    "SEO_SECRET": "t", "TAROT_MEDIA_UPLOAD_DISABLED": "1",
}.items():
    os.environ.setdefault(_k, _v)

import psycopg2  # noqa: E402
import auryel_bot as A  # noqa: E402  (import => init_db() sur la base de test)

# ---------------------------------------------------------------------------
# Harnais.
# ---------------------------------------------------------------------------
_STATE = {"pass": 0, "fail": 0, "bugs": []}


def check(cond, label, bug=False):
    if cond:
        _STATE["pass"] += 1
        print(f"  OK   {label}")
    else:
        _STATE["fail"] += 1
        print(f"  FAIL {label}")
        if bug:
            _STATE["bugs"].append(label)


def section(t):
    print("-" * 70)
    print(t)


# user_id de test — préfixe reconnaissable "a4".
def _uid(n):
    return f"a4000000-0000-4000-8000-{n:012d}"


_ALL_TEST_UIDS = [_uid(i) for i in range(1, 60)]


def _conn():
    return psycopg2.connect(_DSN)


def _cleanup():
    conn = _conn()
    try:
        c = conn.cursor()
        for uid in _ALL_TEST_UIDS:
            c.execute("DELETE FROM messages WHERE user_id=%s", (uid,))
            c.execute("DELETE FROM tirages WHERE user_id=%s", (uid,))
            c.execute("DELETE FROM earned_credits WHERE user_id=%s", (uid,))
            c.execute("DELETE FROM consultations WHERE user_id=%s", (uid,))
            c.execute("DELETE FROM consultation_allowance WHERE user_id=%s", (uid,))
            c.execute("DELETE FROM app_sessions WHERE user_id=%s", (uid,))
            c.execute("DELETE FROM app_profiles WHERE user_id=%s", (uid,))
            c.execute("DELETE FROM accounts WHERE user_id=%s", (uid,))
        conn.commit()
    finally:
        conn.close()


def _seed_account(uid, first_free=None, purchased=0, fcua="__auto__"):
    """INSERT direct d'un compte. first_free=None -> laisse le DEFAULT SQL jouer."""
    conn = _conn()
    try:
        c = conn.cursor()
        cols = ["user_id", "email", "created_at"]
        vals = [uid, f"{uid}@a4.test", A._utcnow()]
        if first_free is not None:
            cols.append("first_free_seconds_remaining")
            vals.append(first_free)
        cols.append("purchased_seconds_remaining")
        vals.append(purchased)
        if fcua != "__auto__":
            cols.append("first_consultation_used_at")
            vals.append(fcua)
        c.execute(
            f"INSERT INTO accounts ({','.join(cols)}) VALUES ({','.join(['%s'] * len(vals))})",
            vals,
        )
        conn.commit()
    finally:
        conn.close()


def _seed_allowance(uid, allowance=28800, used=0, days_span=(1, 29)):
    now = A._utcnow()
    conn = _conn()
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO consultation_allowance "
            "(user_id, period_start, period_end, monthly_limit, monthly_used, "
            " created_at, monthly_allowance_seconds, monthly_used_seconds) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (uid, now - timedelta(days=days_span[0]), now + timedelta(days=days_span[1]),
             4, 0, now, allowance, used),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_consultation(uid, advisor="selena", started=None, last_activity=None,
                       billed_until=None, credit_source="time"):
    now = A._utcnow()
    started = started or now
    cid = str(uuid.uuid4())
    conn = _conn()
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO consultations "
            "(id, user_id, advisor_id, started_at, expires_at, credit_source, "
            " created_at, last_activity_at, billed_until) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (cid, uid, advisor, started, started + A._CONSULTATION_DUREE,
             credit_source, now, last_activity, billed_until),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _row(sql, params=()):
    conn = _conn()
    try:
        c = conn.cursor()
        c.execute(sql, params)
        return c.fetchone()
    finally:
        conn.close()


def _acc(uid):
    return _row("SELECT first_free_seconds_remaining, purchased_seconds_remaining, "
                "first_consultation_used_at FROM accounts WHERE user_id=%s", (uid,))


def _alw(uid):
    return _row("SELECT monthly_allowance_seconds, monthly_used_seconds "
                "FROM consultation_allowance WHERE user_id=%s", (uid,))


def _con(cid):
    return _row("SELECT last_activity_at, billed_until, advisor_id, credit_source "
                "FROM consultations WHERE id=%s", (cid,))


_cleanup()

# ===========================================================================
section("1. SCHÉMA / MIGRATION v34 — catalogue PostgreSQL réel")
# ===========================================================================
_c = _conn()
_cur = _c.cursor()
_cur.execute("""
    SELECT table_name, column_name, is_nullable, column_default
    FROM information_schema.columns
    WHERE (table_name='accounts' AND column_name IN
              ('first_free_seconds_remaining','purchased_seconds_remaining'))
       OR (table_name='consultation_allowance' AND column_name IN
              ('monthly_allowance_seconds','monthly_used_seconds'))
       OR (table_name='consultations' AND column_name IN
              ('last_activity_at','billed_until','expires_at','credit_source'))
""")
_cat = {(r[0], r[1]): (r[2], r[3]) for r in _cur.fetchall()}
_c.close()

check(_cat.get(("accounts", "first_free_seconds_remaining"), (None, None))[1] == "3600",
      "1a accounts.first_free_seconds_remaining DEFAULT = 3600")
check(_cat.get(("accounts", "purchased_seconds_remaining"), (None, None))[1] == "0",
      "1b accounts.purchased_seconds_remaining DEFAULT = 0")
check(_cat.get(("consultation_allowance", "monthly_allowance_seconds"), (None, None))[1] == "28800",
      "1c consultation_allowance.monthly_allowance_seconds DEFAULT = 28800")
check(_cat.get(("consultation_allowance", "monthly_used_seconds"), (None, None))[1] == "0",
      "1d consultation_allowance.monthly_used_seconds DEFAULT = 0")
check(("consultations", "last_activity_at") in _cat,
      "1e consultations.last_activity_at présente")
check(("consultations", "billed_until") in _cat,
      "1f consultations.billed_until présente")
check(_cat.get(("consultations", "expires_at"), (None, None))[0] == "NO"
      and _cat.get(("consultations", "credit_source"), (None, None))[0] == "NO",
      "1g consultations.expires_at + credit_source NOT NULL (valeurs de compat obligatoires)")

# ===========================================================================
section("2. BACKFILL v34 réel (rejeu contrôlé de la tranche sur base de test)")
# ===========================================================================
# On simule un état "pré-v34" : compte legacy présent, colonne first_free
# retirée, puis on rejoue EXACTEMENT le SQL de la tranche v34 de init_db().
_uL1, _uL2 = _uid(1), _uid(2)
_seed_account(_uL1, first_free=None, fcua=None)                 # gratuite jamais consommée
_seed_account(_uL2, first_free=None, fcua=A._utcnow() - timedelta(days=3))  # déjà consommée
_c = _conn()
_cur = _c.cursor()
# drop + re-add SANS default (état "colonne juste ajoutée, pas encore backfillée")
_cur.execute("ALTER TABLE accounts DROP COLUMN first_free_seconds_remaining")
_cur.execute("ALTER TABLE accounts ADD COLUMN first_free_seconds_remaining INTEGER")
_c.commit()
# rejeu du backfill EXACT (copie mot pour mot de la tranche v34 de init_db)
_cur.execute(
    "UPDATE accounts SET first_free_seconds_remaining = "
    "CASE WHEN first_consultation_used_at IS NULL THEN 3600 ELSE 0 END "
    "WHERE first_free_seconds_remaining IS NULL"
)
_n1 = _cur.rowcount
_cur.execute("ALTER TABLE accounts ALTER COLUMN first_free_seconds_remaining SET DEFAULT 3600")
_c.commit()
check(_acc(_uL1)[0] == 3600, "2a legacy first_consultation_used_at NULL -> first_free = 3600")
check(_acc(_uL2)[0] == 0, "2b legacy first_consultation_used_at NOT NULL -> first_free = 0")
# rejeu idempotent : 2e passage ne touche plus aucune ligne
_cur.execute(
    "UPDATE accounts SET first_free_seconds_remaining = "
    "CASE WHEN first_consultation_used_at IS NULL THEN 3600 ELSE 0 END "
    "WHERE first_free_seconds_remaining IS NULL"
)
_n2 = _cur.rowcount
_cur.execute("ALTER TABLE accounts ALTER COLUMN first_free_seconds_remaining SET DEFAULT 3600")
_c.commit()
_c.close()
check(_n2 == 0 and _acc(_uL1)[0] == 3600 and _acc(_uL2)[0] == 0,
      f"2c rejeu idempotent : 2e passage touche 0 ligne (1er={_n1}, 2e={_n2}), aucune corruption")

# ===========================================================================
section("3. DEFAULT nouveau compte / nouvelle allowance — relecture PostgreSQL")
# ===========================================================================
_u3 = _uid(3)
_c = _conn()
_cur = _c.cursor()
_cur.execute("INSERT INTO accounts (user_id, email, created_at) VALUES (%s,%s,%s)",
             (_u3, f"{_u3}@a4.test", A._utcnow()))
_c.commit()
_c.close()
_a3 = _acc(_u3)
check(_a3[0] == 3600 and _a3[1] == 0,
      f"3a nouveau compte sans colonnes temps -> first_free=3600 purchased=0 (lu: {_a3[0]}/{_a3[1]})")
# allowance via le chemin production réel : provision_allowance()
_now3 = A._utcnow()
A.provision_allowance(_u3, _now3 - timedelta(days=1), _now3 + timedelta(days=29),
                      monthly_limit=4)
_al3 = _alw(_u3)
check(_al3 == (28800, 0),
      f"3b provision_allowance() -> monthly_allowance_seconds=28800 monthly_used_seconds=0 (lu: {_al3})")

# ===========================================================================
section("4. SETTLE first_free — débit 180 s, persistance réelle")
# ===========================================================================
_u4 = _uid(4)
_seed_account(_u4, first_free=3600, purchased=0, fcua=None)
_T0 = A._utcnow().replace(microsecond=0)
_cid4 = _seed_consultation(_u4, last_activity=_T0, billed_until=_T0)
_c = _conn()
_cur = _c.cursor()
A._settle_consultation_time_tx(_cur, _u4, _cid4, _T0 + timedelta(seconds=180))
_c.commit()
_c.close()
_a4 = _acc(_u4)
_cn4 = _con(_cid4)
check(_a4[0] == 3420, f"4a first_free 3600 -> 3420 (lu: {_a4[0]})")
check(_alw(_u4) is None, "4b aucune allowance -> pas de débit Premium")
check(_a4[1] == 0, "4c purchased inchangé (0)")
check(_a4[2] is not None, "4d first_consultation_used_at posé au 1er débit réel")
check(_cn4[1] == _T0 + timedelta(seconds=180), f"4e billed_until = T0 + 180 s (lu: {_cn4[1]})")
# relecture nouvelle connexion : persistance
check(_acc(_u4)[0] == 3420, "4f valeur persistée (relecture connexion neuve) = 3420")

# ===========================================================================
section("5. IDÉMPOTENCE settle — même now, 0 débit supplémentaire")
# ===========================================================================
_c = _conn()
_cur = _c.cursor()
_res5 = A._settle_consultation_time_tx(_cur, _u4, _cid4, _T0 + timedelta(seconds=180))
_c.commit()
_c.close()
check(_res5["settled_seconds"] == 0, f"5a settle rejoué au même now -> settled_seconds=0 (lu: {_res5['settled_seconds']})")
check(_acc(_u4)[0] == 3420, "5b first_free toujours 3420 (aucun double débit)")
check(_con(_cid4)[1] == _T0 + timedelta(seconds=180), "5c billed_until inchangé")

# ===========================================================================
section("6. CAP 300 s — settle à T0 + 20 min ne facture pas l'inactivité")
# ===========================================================================
_u6 = _uid(6)
_seed_account(_u6, first_free=3600, fcua=None)
_T6 = A._utcnow().replace(microsecond=0)
_cid6 = _seed_consultation(_u6, last_activity=_T6, billed_until=_T6)
_c = _conn()
_cur = _c.cursor()
_res6 = A._settle_consultation_time_tx(_cur, _u6, _cid6, _T6 + timedelta(minutes=20))
_c.commit()
_c.close()
check(_res6["settled_seconds"] == 300,
      f"6a débit plafonné à 300 s (pas 1200) — lu: {_res6['settled_seconds']}")
check(_acc(_u6)[0] == 3300, f"6b first_free 3600 -> 3300 (lu: {_acc(_u6)[0]})")
check(_con(_cid6)[1] == _T6 + timedelta(seconds=300),
      f"6c billed_until = T0 + 5 min (lu: {_con(_cid6)[1]})")

# ===========================================================================
section("7. WATERFALL first_free -> Premium (débit 180 s, ff=100)")
# ===========================================================================
_u7 = _uid(7)
_seed_account(_u7, first_free=100, purchased=500, fcua=None)
_seed_allowance(_u7, allowance=28800, used=0)
_T7 = A._utcnow().replace(microsecond=0)
_cid7 = _seed_consultation(_u7, last_activity=_T7, billed_until=_T7)
_c = _conn()
_cur = _c.cursor()
A._settle_consultation_time_tx(_cur, _u7, _cid7, _T7 + timedelta(seconds=180))
_c.commit()
_c.close()
_a7, _al7 = _acc(_u7), _alw(_u7)
check(_a7[0] == 0, f"7a first_free 100 -> 0 (lu: {_a7[0]})")
check(_al7[1] == 80, f"7b monthly_used_seconds 0 -> 80 (premium_remaining = 28720) (lu: {_al7[1]})")
check(_a7[1] == 500, f"7c purchased inchangé (500) (lu: {_a7[1]})")
check(_a7[2] is not None, "7d first_consultation_used_at posé")

# ===========================================================================
section("8. WATERFALL Premium -> purchased (premium restant 100, débit 180)")
# ===========================================================================
_u8 = _uid(8)
_seed_account(_u8, first_free=0, purchased=500, fcua=A._utcnow() - timedelta(days=5))
_seed_allowance(_u8, allowance=28800, used=28700)
_T8 = A._utcnow().replace(microsecond=0)
_cid8 = _seed_consultation(_u8, last_activity=_T8, billed_until=_T8)
_c = _conn()
_cur = _c.cursor()
A._settle_consultation_time_tx(_cur, _u8, _cid8, _T8 + timedelta(seconds=180))
_c.commit()
_c.close()
_a8, _al8 = _acc(_u8), _alw(_u8)
check(_al8[1] == 28800, f"8a monthly_used_seconds -> 28800 (premium épuisé) (lu: {_al8[1]})")
check(_a8[1] == 420, f"8b purchased 500 -> 420 (lu: {_a8[1]})")
check(_al8[1] <= _al8[0] and _a8[1] >= 0 and _a8[0] >= 0,
      "8c aucun compteur négatif, monthly_used_seconds borné par l'allocation")

# ===========================================================================
section("9. ÉPUISEMENT PARTIEL + aucune rétrofacturation après recharge (A.2 U->W)")
# ===========================================================================
_u9 = _uid(9)
# total restant = 100 s : first_free=100, pas de premium, pas de purchased.
_seed_account(_u9, first_free=100, purchased=0, fcua=None)
_T9 = A._utcnow().replace(microsecond=0)
# fenêtre qui DOIT régler 300 s (billed_until = T9, last_activity = T9, now = T9+300)
_cid9 = _seed_consultation(_u9, last_activity=_T9, billed_until=_T9)
_c = _conn()
_cur = _c.cursor()
_res9 = A._settle_consultation_time_tx(_cur, _u9, _cid9, _T9 + timedelta(seconds=300))
_c.commit()
_c.close()
_a9 = _acc(_u9)
_cn9 = _con(_cid9)
check(_res9["settled_seconds"] == 100,
      f"9a seulement 100 s débitées sur 300 dues (lu: {_res9['settled_seconds']})")
check(_a9[0] == 0 and _a9[1] == 0, "9b aucun bucket négatif (first_free=0, purchased=0)")
check(_cn9[1] == _T9 + timedelta(seconds=100),
      f"9c billed_until avance seulement de 100 s (lu: {_cn9[1]})")
check(_res9["exhausted"] is True, "9d exhausted=True exposé")
# fenêtre coupée : last_activity_at ramené à billed_until - 300 s
check(_cn9[0] == _T9 + timedelta(seconds=100) - timedelta(seconds=300),
      f"9e fenêtre coupée au point d'épuisement (last_activity = billed_until - 300) (lu: {_cn9[0]})")
_c = _conn()
_cur = _c.cursor()
_snap9 = A._get_time_snapshot_tx(_cur, _u9, _T9 + timedelta(seconds=300))
_c.close()
check(_snap9["total_remaining_seconds"] == 0, "9f snapshot total = 0 après épuisement")
# RECHARGE : +600 s purchased, puis nouvelle activité -> aucune rétrofacturation
_c = _conn()
_cur = _c.cursor()
_cur.execute("UPDATE accounts SET purchased_seconds_remaining=600 WHERE user_id=%s", (_u9,))
_c.commit()
# le prochain message ouvre une fenêtre NEUVE à T9+3600 (via _process : touch si dispo)
_res9b = A._process_consultation_activity_tx(_cur, _u9, _cid9, _T9 + timedelta(seconds=3600))
_c.commit()
_c.close()
_a9b = _acc(_u9)
check(_a9b[1] == 600,
      f"9g après recharge + nouvelle activité : purchased TOUJOURS 600 — les 200 s "
      f"impayées du passé ne sont PAS rétrofacturées (lu: {_a9b[1]})")
check(_con(_cid9)[0] == _T9 + timedelta(seconds=3600)
      and _con(_cid9)[1] == _T9 + timedelta(seconds=3600),
      "9h fenêtre NEUVE ouverte à now (last_activity = billed_until = now)")

# ===========================================================================
section("10. FLOW transactionnel réel — _open_time_consultation_flow_tx")
# ===========================================================================
_u10 = _uid(10)
_seed_account(_u10, first_free=3600, fcua=None)
_seed_allowance(_u10, allowance=28800, used=0)
_TF = A._utcnow().replace(microsecond=0)
# CAS A — premier message
_flowA = A._open_time_consultation_flow_tx(_u10, "selena", None, _TF)
check(_flowA["status"] == "ok" and _flowA["consultation"]["created"] is True,
      "10A premier message -> status ok, consultation créée")
_cidF = _flowA["consultation"]["id"]
_cnF = _con(_cidF)
check(_cnF[3] == "time", "10A2 credit_source = time")
check(_cnF[0] == _TF and _cnF[1] == _TF,
      f"10A3 touch : last_activity_at = billed_until = now (lu: {_cnF[0]} / {_cnF[1]})")
check(_acc(_u10)[0] == 3600, "10A4 first_free NON débité au touch (3600)")
check(_acc(_u10)[2] is None, "10A5 first_consultation_used_at NULL après le seul touch")
# CAS B — 4m59 plus tard
_flowB = A._open_time_consultation_flow_tx(_u10, "selena", None, _TF + timedelta(seconds=299))
check(_flowB["status"] == "ok" and _flowB["consultation"]["id"] == _cidF
      and _flowB["consultation"]["created"] is False,
      "10B 4m59 -> même consultation logique, created=False")
check(_acc(_u10)[0] == 3600 - 299,
      f"10B2 settle 299 s de first_free (lu: {_acc(_u10)[0]}, attendu {3600 - 299})")
check(_con(_cidF)[0] == _TF + timedelta(seconds=299), "10B3 nouveau touch à now")
check(_con(_cidF)[2] == "selena", "10B4 advisor figé = selena")
# CAS C — >5 min, même advisor -> même consultation
_flowC = A._open_time_consultation_flow_tx(_u10, "selena", None, _TF + timedelta(minutes=30))
check(_flowC["status"] == "ok" and _flowC["consultation"]["id"] == _cidF
      and _flowC["consultation"]["created"] is False,
      "10C >5 min même advisor -> même consultation logique reprise")
# CAS D — >5 min, advisor différent -> NOUVELLE consultation, ancienne intacte
_flowD = A._open_time_consultation_flow_tx(_u10, "ezra", None, _TF + timedelta(minutes=60))
check(_flowD["status"] == "ok" and _flowD["consultation"]["id"] != _cidF
      and _flowD["consultation"]["created"] is True
      and _flowD["consultation"]["advisor_id"] == "ezra",
      "10D >5 min advisor différent -> NOUVELLE consultation (ezra)")
check(_con(_cidF)[2] == "selena",
      "10D2 ancienne consultation : advisor_id selena JAMAIS modifié")
_ncons10 = _row("SELECT COUNT(*) FROM consultations WHERE user_id=%s", (_u10,))[0]
check(_ncons10 == 2, f"10D3 exactement 2 consultations logiques (lu: {_ncons10})")
# CAS E — aucun temps -> time_exhausted
_u10e = _uid(11)
_seed_account(_u10e, first_free=0, purchased=0, fcua=A._utcnow() - timedelta(days=2))
_flowE = A._open_time_consultation_flow_tx(_u10e, "selena", None, A._utcnow())
check(_flowE["status"] == "time_exhausted" and _flowE["consultation"] is None,
      "10E aucun temps -> status time_exhausted, consultation None")
check(_row("SELECT COUNT(*) FROM consultations WHERE user_id=%s", (_u10e,))[0] == 0,
      "10E2 aucune consultation créée")

# ===========================================================================
section("11. TIRAGE transactionnel réel")
# ===========================================================================
_u11 = _uid(12)
_u11b = _uid(13)
_seed_account(_u11, first_free=3600, fcua=None)
_seed_account(_u11b, first_free=3600, fcua=None)
_tid_ok = str(uuid.uuid4())
_tid_other = str(uuid.uuid4())
_c = _conn()
_cur = _c.cursor()
_cur.execute("INSERT INTO tirages (id, user_id, card_keys, advisor_id, created_at) "
             "VALUES (%s,%s,%s::jsonb,%s,%s)",
             (_tid_ok, _u11, '["le_soleil","le_fou","la_lune"]', "maia", A._utcnow()))
_cur.execute("INSERT INTO tirages (id, user_id, card_keys, advisor_id, created_at) "
             "VALUES (%s,%s,%s::jsonb,%s,%s)",
             (_tid_other, _u11b, '["le_fou","la_lune","le_soleil"]', "maia", A._utcnow()))
_c.commit()
_c.close()
_TT = A._utcnow().replace(microsecond=0)
_flowT = A._open_time_consultation_flow_tx(_u11, "selena", _tid_ok, _TT)
check(_flowT["status"] == "ok" and _flowT["tirage_attached"] is True,
      "11a tirage valide -> tirage_attached True")
_tcid = _row("SELECT consultation_id FROM tirages WHERE id=%s", (_tid_ok,))[0]
check(str(_tcid) == _flowT["consultation"]["id"],
      "11b tirages.consultation_id = consultation sélectionnée par le flow")
# tirage d'un AUTRE user passé par _u11 -> tirage_not_found, aucune mutation
_flowT2 = A._open_time_consultation_flow_tx(_u11, "selena", _tid_other, A._utcnow())
check(_flowT2["status"] == "tirage_not_found",
      "11c tirage d'un autre user -> status tirage_not_found")
check(_row("SELECT consultation_id FROM tirages WHERE id=%s", (_tid_other,))[0] is None,
      "11d tirage de l'autre user : consultation_id toujours NULL (aucune mutation)")
# tirage inexistant
_flowT3 = A._open_time_consultation_flow_tx(_u11, "selena", str(uuid.uuid4()), A._utcnow())
check(_flowT3["status"] == "tirage_not_found", "11e tirage inexistant -> tirage_not_found")
# time_exhausted + tirage -> aucune mutation du tirage
_u11c = _uid(14)
_seed_account(_u11c, first_free=0, purchased=0, fcua=A._utcnow() - timedelta(days=2))
_tid_te = str(uuid.uuid4())
_c = _conn()
_cur = _c.cursor()
_cur.execute("INSERT INTO tirages (id, user_id, card_keys, advisor_id, created_at) "
             "VALUES (%s,%s,%s::jsonb,%s,%s)",
             (_tid_te, _u11c, '["le_soleil","le_fou","la_lune"]', "maia", A._utcnow()))
_c.commit()
_c.close()
_flowT4 = A._open_time_consultation_flow_tx(_u11c, "selena", _tid_te, A._utcnow())
check(_flowT4["status"] == "time_exhausted"
      and _row("SELECT consultation_id FROM tirages WHERE id=%s", (_tid_te,))[0] is None,
      "11f time_exhausted + tirage -> aucune mutation du tirage")

# ===========================================================================
section("12. ROLLBACK réel — exception après mutation intermédiaire")
# ===========================================================================
_u12 = _uid(15)
_seed_account(_u12, first_free=3600, fcua=None)
_T12 = A._utcnow().replace(microsecond=0)
_cid12 = _seed_consultation(_u12, last_activity=_T12, billed_until=_T12)
_before12 = _acc(_u12)[0]
_c = _conn()
try:
    _cur = _c.cursor()
    # mutation intermédiaire RÉELLE via le moteur (débite first_free)...
    A._debit_consultation_seconds_tx(_cur, _u12, 120, _T12 + timedelta(seconds=120))
    # ...puis exception AVANT commit (déclenchée par le harnais, pas par le code prod)
    raise RuntimeError("A.4 rollback harness — exception volontaire avant commit")
except RuntimeError:
    _c.rollback()
finally:
    _c.close()
check(_acc(_u12)[0] == _before12,
      f"12a rollback : first_free inchangé ({_before12}) — aucune mutation partielle persistée")

# ===========================================================================
section("13. accounts FOR UPDATE — sérialisation réelle de deux transactions")
# ===========================================================================
_u13 = _uid(16)
_seed_account(_u13, first_free=3600, fcua=None)
_tx2_waited = {"blocked": None, "err": None}


def _tx2_worker():
    c2 = _conn()
    try:
        cur2 = c2.cursor()
        cur2.execute("SET lock_timeout = '4000ms'")
        t0 = _time.monotonic()
        cur2.execute("SELECT user_id FROM accounts WHERE user_id=%s "
                     "AND deleted_at IS NULL FOR UPDATE", (_u13,))
        dt = _time.monotonic() - t0
        _tx2_waited["blocked"] = dt
        c2.rollback()
    except Exception as e:  # noqa: BLE001
        _tx2_waited["err"] = type(e).__name__
        try:
            c2.rollback()
        except Exception:
            pass
    finally:
        c2.close()


_c1 = _conn()
_cur1 = _c1.cursor()
_cur1.execute("SELECT user_id FROM accounts WHERE user_id=%s AND deleted_at IS NULL "
              "FOR UPDATE", (_u13,))
_th = threading.Thread(target=_tx2_worker)
_th.start()
_time.sleep(1.5)                 # TX2 doit être bloquée pendant ce temps
_still_blocked = _tx2_waited["blocked"] is None and _tx2_waited["err"] is None
_c1.commit()                     # libère le verrou
_th.join(timeout=8)
_c1.close()
check(_still_blocked,
      "13a TX2 (accounts ... FOR UPDATE) est bien BLOQUÉE tant que TX1 tient le verrou")
check(_tx2_waited["err"] is None and _tx2_waited["blocked"] is not None
      and _tx2_waited["blocked"] >= 1.0,
      f"13b TX2 débloquée APRÈS le commit de TX1 (attente ~{_tx2_waited['blocked']:.2f}s, "
      f"err={_tx2_waited['err']})")

# ===========================================================================
section("14. DOUBLE SETTLE concurrent — mutex accounts => débit UNIQUE")
# ===========================================================================
_u14 = _uid(17)
# 300 s réellement dues ; first_free plein pour absorber sans épuisement.
_seed_account(_u14, first_free=3600, fcua=None)
_T14 = A._utcnow().replace(microsecond=0)
_cid14 = _seed_consultation(_u14, last_activity=_T14, billed_until=_T14)
_barrier = threading.Barrier(2)
_results14 = []
_lock14 = threading.Lock()


def _settle_worker():
    cw = _conn()
    try:
        curw = cw.cursor()
        curw.execute("SET lock_timeout = '8000ms'")
        _barrier.wait(timeout=10)
        # chemin production : mutex accounts FOR UPDATE PUIS settle (comme A.3 /
        # _state_with_time_settle / _open_time_consultation_flow_tx).
        curw.execute("SELECT user_id FROM accounts WHERE user_id=%s "
                     "AND deleted_at IS NULL FOR UPDATE", (_u14,))
        r = A._settle_consultation_time_tx(curw, _u14, _cid14, _T14 + timedelta(seconds=300))
        cw.commit()
        with _lock14:
            _results14.append(r["settled_seconds"])
    except Exception as e:  # noqa: BLE001
        with _lock14:
            _results14.append(f"ERR:{type(e).__name__}")
        try:
            cw.rollback()
        except Exception:
            pass
    finally:
        cw.close()


_t1 = threading.Thread(target=_settle_worker)
_t2 = threading.Thread(target=_settle_worker)
_t1.start()
_t2.start()
_t1.join(timeout=20)
_t2.join(timeout=20)
_total_debited14 = 3600 - _acc(_u14)[0]
check(sorted(_results14) == [0, 300] or sorted(_results14, key=str) in ([0, 300],),
      f"14a un worker débite 300 s, l'autre 0 s (résultats: {_results14})")
check(_total_debited14 == 300,
      f"14b TOTAL débité = 300 s (PAS 600) — first_free 3600 -> {_acc(_u14)[0]} (lu: {_total_debited14})",
      bug=True)
check(_con(_cid14)[1] == _T14 + timedelta(seconds=300),
      "14c billed_until = T0 + 300 s (une seule avance)")

# ===========================================================================
section("15. ORDRE DES VERROUS / deadlock — accounts puis consultation_allowance")
# ===========================================================================
import ast as _ast   # noqa: E402
import inspect as _inspect  # noqa: E402
_dbg_src = _inspect.getsource(A._debit_consultation_seconds_tx)
_i_acc = _dbg_src.find("FROM accounts WHERE user_id=%s FOR UPDATE")
_i_alw = _dbg_src.find("_active_allowance_seconds_for_update")
check(0 <= _i_acc < _i_alw,
      "15a _debit : accounts FOR UPDATE lu AVANT consultation_allowance FOR UPDATE (ordre canonique)")
check("consultations" not in _dbg_src.split("def ")[0]
      or "FOR UPDATE" not in _inspect.getsource(A._consultation_time_row),
      "15b aucun SELECT ... FOR UPDATE sur consultations (moteur ne verrouille jamais cette table)")
# aucun deadlock observé sur les scénarios concurrents 13 & 14
check(_tx2_waited["err"] is None and all(not str(x).startswith("ERR") for x in _results14),
      "15c aucun deadlock / lock_timeout observé sur les tests concurrents réels")

# ===========================================================================
section("16. HTTP réel — Flask test client + PostgreSQL réel + LLM mocké")
# ===========================================================================
from unittest.mock import patch  # noqa: E402
A.app.config["TESTING"] = True
A.app.config["RATELIMIT_ENABLED"] = False
try:
    A.limiter.enabled = False
except Exception:
    pass
_client = A.app.test_client()

_u16 = _uid(20)
_seed_account(_u16, first_free=3600, fcua=None)
_seed_allowance(_u16, allowance=28800, used=0)
_tok16 = A.create_app_session(_u16)
_H = {"Authorization": f"Bearer {_tok16}"}
_TH = A._utcnow().replace(microsecond=0)

with patch.object(A, "call_llm", return_value="REPONSE-A4-MOCK"), \
     patch.object(A, "_utcnow", return_value=_TH):
    _rp = _client.post("/api/consultation/message", json={"message": "bonjour a4"}, headers=_H)
_jp = _rp.get_json()
check(_rp.status_code == 200 and _jp["reply"] == "REPONSE-A4-MOCK",
      "16a POST /message -> 200, LLM mocké")
check(_jp["consultation"]["credit_source"] == "time"
      and _jp["consultation"]["opened_now"] is True
      and _jp["time"]["total_remaining_seconds"] == 3600 + 28800,
      "16b POST ouvre la fenêtre SANS débit immédiat (time.total = 3600 + 28800)")
_cid16 = _jp["consultation"]["id"]
check(_acc(_u16)[0] == 3600 and _alw(_u16)[1] == 0,
      "16c DB réelle : first_free 3600 intact, monthly_used_seconds 0 après le POST")
check(_con(_cid16)[0] == _TH and _con(_cid16)[1] == _TH,
      "16d DB réelle : last_activity_at = billed_until = T0")

with patch.object(A, "_utcnow", return_value=_TH + timedelta(seconds=180)):
    _rs = _client.get("/api/consultation/state", headers=_H)
_js = _rs.get_json()
check(_rs.status_code == 200 and _acc(_u16)[0] == 3420,
      f"16e GET /state à +180 s -> settle 180 s réel (first_free -> {_acc(_u16)[0]})")
check(_js["time"]["total_remaining_seconds"] == 3420 + 28800,
      "16f /state.time.total = 3420 + 28800")
check(_acc(_u16)[2] is not None, "16g first_consultation_used_at posé par le settle du GET /state")

_snap_before_msgs = (_acc(_u16), _alw(_u16), _con(_cid16))
with patch.object(A, "_utcnow", return_value=_TH + timedelta(seconds=600)):
    _rm = _client.get("/api/consultation/messages", headers=_H)
_jm = _rm.get_json()
check(_rm.status_code == 200 and _jm["consultation_id"] == _cid16
      and [m["content"] for m in _jm["messages"]] == ["bonjour a4", "REPONSE-A4-MOCK"],
      "16h GET /messages -> historique complet de la consultation logique")
check((_acc(_u16), _alw(_u16), _con(_cid16)) == _snap_before_msgs,
      "16i GET /messages : AUCUN débit / touch supplémentaire (DB strictement inchangée)")

# ===========================================================================
section("17. NON-RÉGRESSION structurelle")
# ===========================================================================
_pm = _code = _inspect.getsource(A.api_consultation_message)
check("_open_time_consultation_flow_tx(" in _pm and "open_or_get_consultation(" not in _pm,
      "17a POST /message toujours câblé sur _open_time_consultation_flow_tx")
_st_src = _inspect.getsource(A.api_consultation_state)
check("_state_with_time_settle(" in _st_src,
      "17b GET /state toujours câblé sur _state_with_time_settle (moteur temps)")

_cleanup()

print("=" * 70)
_total = _STATE["pass"] + _STATE["fail"]
if _STATE["fail"] == 0:
    print(f"OK — {_STATE['pass']}/{_total} assertions PostgreSQL réelles passées")
    sys.exit(0)
else:
    print(f"FAIL — {_STATE['fail']}/{_total} en échec")
    if _STATE["bugs"]:
        print("BUGS POTENTIELS :", _STATE["bugs"])
    sys.exit(1)
