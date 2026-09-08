"""
test_app_wellbeing_journey.py — J7 : « Mon parcours bien-être » (+ correction :
`pensee` = trace dédiée, PAS `share_reward_days` ; crédit via le helper unique
`_reconcile_wellbeing_progress` appelé depuis TOUS les points d'action réels).

Routes / helper testés :
    GET  /api/app/wellbeing/progress
    POST /api/app/wellbeing/mission
    _reconcile_wellbeing_progress(user_id)   (= la SEULE voie de crédit ;
        appelée aussi depuis POST /api/tirages et POST /api/consultation/message)

100 % local : psycopg2 mocké, get_conn -> fausse DB en mémoire modélisant
wellbeing_mission_days (pensee + moment) / tirages / consultations /
wellbeing_cycle_rewards / accounts. AUCUNE table share_reward_days n'intervient.
resolve_app_session monkeypatché, _wellbeing_day figé. Aucun réseau, aucune DB
réelle, aucun LLM. Style aligné sur test_app_rewards_share.py.
"""

import sys
import inspect
from datetime import date, datetime, timedelta, timezone
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


UID1 = "11111111-1111-4111-8111-111111111111"
UID2 = "22222222-2222-4222-8222-222222222222"

# ---------------------------------------------------------------------------
# Fausse DB
#   _MISSION  : {(uid, date, mission_id)}  -> wellbeing_mission_days (pensee|moment)
#   _TIRAGE   : {(uid, date)}              -> tirages (date Europe/Paris de created_at)
#   _CONSULT  : {(uid, date)}              -> consultations (date Europe/Paris de last_activity_at)
#   _CYCLES   : {(uid, cycle_number)}      -> wellbeing_cycle_rewards
#   _ACCOUNTS : uid -> {"purchased": int, "deleted_at": None|ts}
# ---------------------------------------------------------------------------
_MISSION = set()
_TIRAGE = set()
_CONSULT = set()
_CYCLES = set()
_ACCOUNTS = {}
_SQL_SEEN = []


def _reset_db():
    _MISSION.clear(); _TIRAGE.clear(); _CONSULT.clear()
    _CYCLES.clear(); _ACCOUNTS.clear(); _SQL_SEEN.clear()
    _ACCOUNTS[UID1] = {"purchased": 0, "deleted_at": None}
    _ACCOUNTS[UID2] = {"purchased": 0, "deleted_at": None}


def _seed_derived_day(uid, d):
    """Les 2 missions DÉRIVÉES (tirage + consultation) faites le jour `d`."""
    _TIRAGE.add((uid, d)); _CONSULT.add((uid, d))


def _seed_local_day(uid, d):
    """Les 2 missions ENREGISTRÉES (pensee + moment) faites le jour `d`."""
    _MISSION.add((uid, d, "pensee")); _MISSION.add((uid, d, "moment"))


def _seed_full_days(uid, days):
    """`days` journées ENTIÈREMENT complétées, dates décroissantes à partir
    d'hier (pour ne pas empiéter sur 'aujourd'hui' figé)."""
    base = _FAKE_TODAY["d"] - timedelta(days=1)
    for i in range(days):
        d = base - timedelta(days=i)
        _seed_derived_day(uid, d)
        _seed_local_day(uid, d)


class _Cur:
    def __init__(self):
        self._rows = []
        self._one = None
        self.rowcount = -1

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = " ".join(sql.split())
        _SQL_SEEN.append(k)
        p = params
        self._rows = []
        self._one = None
        self.rowcount = -1

        if k in (
            "SELECT user_id FROM accounts WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE",
            "SELECT user_id FROM accounts WHERE user_id=%s AND deleted_at IS NULL",
        ):
            uid = p[0]
            acc = _ACCOUNTS.get(uid)
            self._one = (uid,) if (acc and acc["deleted_at"] is None) else None

        elif k == ("SELECT day_date FROM wellbeing_mission_days "
                   "WHERE user_id=%s AND mission_id=%s"):
            uid, mid = p
            self._rows = [(d,) for (u, d, m) in _MISSION if u == uid and m == mid]

        elif k == ("SELECT (created_at AT TIME ZONE 'Europe/Paris')::date "
                   "FROM tirages WHERE user_id=%s"):
            uid = p[0]
            self._rows = [(d,) for (u, d) in _TIRAGE if u == uid]

        elif k == ("SELECT (last_activity_at AT TIME ZONE 'Europe/Paris')::date "
                   "FROM consultations WHERE user_id=%s AND last_activity_at IS NOT NULL"):
            uid = p[0]
            self._rows = [(d,) for (u, d) in _CONSULT if u == uid]

        elif k == "SELECT cycle_number FROM wellbeing_cycle_rewards WHERE user_id=%s":
            uid = p[0]
            self._rows = [(c,) for (u, c) in _CYCLES if u == uid]

        elif k == ("INSERT INTO wellbeing_mission_days (user_id, day_date, mission_id, created_at) "
                   "VALUES (%s, %s, %s, %s) "
                   "ON CONFLICT (user_id, day_date, mission_id) DO NOTHING"):
            uid, d, mid, _ts = p
            key = (uid, d, mid)
            if key in _MISSION:
                self.rowcount = 0
            else:
                _MISSION.add(key)
                self.rowcount = 1

        elif k == ("INSERT INTO wellbeing_cycle_rewards (user_id, cycle_number, credited_at, credited_seconds) "
                   "VALUES (%s, %s, %s, %s) "
                   "ON CONFLICT (user_id, cycle_number) DO NOTHING"):
            uid, cyc, _ts, _secs = p
            key = (uid, cyc)
            if key in _CYCLES:
                self.rowcount = 0
            else:
                _CYCLES.add(key)
                self.rowcount = 1

        elif k == ("UPDATE accounts SET purchased_seconds_remaining = "
                   "COALESCE(purchased_seconds_remaining, 0) + %s WHERE user_id=%s"):
            secs, uid = p
            acc = _ACCOUNTS.get(uid)
            if acc:
                acc["purchased"] += secs
                self.rowcount = 1
            else:
                self.rowcount = 0

        else:
            raise AssertionError("SQL non géré par le fake wellbeing : " + k)


class _Conn:
    def cursor(self):
        return _Cur()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


A.get_conn = lambda: _Conn()
A.app.config["TESTING"] = True
try:
    A.limiter.enabled = False
except Exception:
    pass
client = A.app.test_client()

_CURRENT = {"uid": UID1}
A.resolve_app_session = lambda tok: (
    {"session_id": "s", "user_id": _CURRENT["uid"], "email": "x@x.co"} if tok else None
)

_FAKE_TODAY = {"d": date(2026, 9, 15)}
_REAL_WELLBEING_DAY = A._wellbeing_day
A._wellbeing_day = lambda now=None: _FAKE_TODAY["d"]


def _as(uid):
    _CURRENT["uid"] = uid


def _hdr():
    return {"Authorization": "Bearer x"}


def _get():
    return client.get("/api/app/wellbeing/progress", headers=_hdr())


def _post(mission_id):
    return client.post("/api/app/wellbeing/mission", headers=_hdr(),
                       json={"mission_id": mission_id})


def _reconcile(uid):
    """Simule un point d'action réel dérivé (tirage sauvé / message envoyé) :
    l'endpoint concerné appelle EXACTEMENT ceci après avoir posé sa trace."""
    return A._reconcile_wellbeing_progress(uid)


# ===========================================================================
# 0. Routes + câblage du helper unique
# ===========================================================================
_rules = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
check(("/api/app/wellbeing/progress", "GET") in _rules,
      "0a GET /api/app/wellbeing/progress enregistrée")
check(("/api/app/wellbeing/mission", "POST") in _rules,
      "0b POST /api/app/wellbeing/mission enregistrée")
check("_reconcile_wellbeing_progress("
      in inspect.getsource(A.api_tirages_create),
      "0c POST /api/tirages appelle _reconcile_wellbeing_progress")
check("_reconcile_wellbeing_progress("
      in inspect.getsource(A.api_consultation_message),
      "0d POST /api/consultation/message appelle _reconcile_wellbeing_progress")
check("_reconcile_wellbeing_progress("
      in inspect.getsource(A.api_wellbeing_mission),
      "0e POST /api/app/wellbeing/mission appelle _reconcile_wellbeing_progress")
check("share_reward" not in inspect.getsource(A._reconcile_wellbeing_progress),
      "0f _reconcile n'utilise JAMAIS share_reward_days")

# ===========================================================================
# 1. « Pensée du jour » : source d'autorité = wellbeing_mission_days, PAS le partage
# ===========================================================================
_reset_db(); _as(UID1)
j = _post("pensee").get_json()
check(j["today"]["missions"][0] == {"id": "pensee", "completed": True},
      "1a POST {pensee} -> mission pensee marquée faite")
check(("SELECT day_date FROM wellbeing_mission_days WHERE user_id=%s AND mission_id=%s"
       in _SQL_SEEN),
      "1b pensee lit wellbeing_mission_days")
check(not any("share_reward_days" in s for s in _SQL_SEEN),
      "1c AUCUNE requête share_reward_days pour compléter pensee")
check((UID1, _FAKE_TODAY["d"], "pensee") in _MISSION,
      "1d une ligne wellbeing_mission_days(pensee) a été écrite")

# ===========================================================================
# 2. Partager SANS consulter la Pensée -> pensee NON complétée
# ===========================================================================
_reset_db(); _as(UID1)
# les 3 autres missions du jour sont faites, mais pas `pensee`
_seed_derived_day(UID1, _FAKE_TODAY["d"])          # tirage + consultation
_MISSION.add((UID1, _FAKE_TODAY["d"], "moment"))   # moment
j = _get().get_json()
check(j["today"]["missions"][0]["completed"] is False,
      "2a pensee reste À FAIRE tant qu'elle n'est pas consultée")
check(j["today"]["completed"] is False, "2b 3/4 -> journée non complétée")
check(j["completed_days_total"] == 0, "2c aucune journée ajoutée")

# ===========================================================================
# 3. mission dérivée sans trace -> 409 ; 4. mission_id invalide -> 400
# ===========================================================================
_reset_db(); _as(UID1)
r = _post("tirage")
check(r.status_code == 409 and r.get_json().get("error") == "mission_action_missing",
      "3 POST {tirage} sans tirage sauvé aujourd'hui -> 409 mission_action_missing")
r = _post("humeur")
check(r.status_code == 400 and r.get_json().get("error") == "invalid_mission",
      "4 mission_id inconnu -> 400 invalid_mission")

# ===========================================================================
# 5. 4/4 le même jour = +1 journée
# ===========================================================================
_reset_db(); _as(UID1)
today = _FAKE_TODAY["d"]
_seed_derived_day(UID1, today)
_post("pensee")
j = _post("moment").get_json()
check(j["today"]["completed"] is True and j["completed_days_total"] == 1,
      "5 pensee + moment + (tirage/consultation dérivés) -> journée complétée, +1")

# ===========================================================================
# 6. idempotence des missions
# ===========================================================================
check(_post("moment").get_json()["completed_days_total"] == 1,
      "6a moment rejoué -> toujours 1 journée")
check(_post("pensee").get_json()["completed_days_total"] == 1,
      "6b pensee rejouée -> toujours 1 journée")

# ===========================================================================
# 7. « 4e mission = CONSULTATION » : la journée devient complète automatiquement
#    (l'endpoint consultation/message appelle _reconcile après avoir posé
#    last_activity_at).
# ===========================================================================
_reset_db(); _as(UID1)
_MISSION.add((UID1, today, "pensee"))
_MISSION.add((UID1, today, "moment"))
_TIRAGE.add((UID1, today))
# 3/4 pour l'instant
check(_get().get_json()["completed_days_total"] == 0, "7a 3/4 -> pas encore de journée")
# l'utilisateur envoie un message de consultation -> trace posée + reconcile
_CONSULT.add((UID1, today))
rec = _reconcile(UID1)
check(rec["completed_days_total"] == 1, "7b consultation = 4e mission -> journée complète")
j = _get().get_json()
check(j["today"]["completed"] is True and j["completed_days_total"] == 1,
      "7c GET reflète la journée complétée sans nouveau POST wellbeing")

# ===========================================================================
# 8. « 4e mission = TIRAGE » : idem, via /api/tirages -> _reconcile
# ===========================================================================
_reset_db(); _as(UID1)
_MISSION.add((UID1, today, "pensee"))
_MISSION.add((UID1, today, "moment"))
_CONSULT.add((UID1, today))
check(_get().get_json()["completed_days_total"] == 0, "8a 3/4 -> pas encore de journée")
_TIRAGE.add((UID1, today))                 # tirage sauvé
rec = _reconcile(UID1)
check(rec["completed_days_total"] == 1, "8b tirage = 4e mission -> journée complète")

# ===========================================================================
# 9. 30e journée complétée par CHAQUE type de 4e action -> +900 s IMMÉDIATEMENT
# ===========================================================================
def _cycle30_via(fourth):
    _reset_db(); _as(UID1)
    _seed_full_days(UID1, 29)               # 29 journées déjà complétées
    # aujourd'hui : 3 missions faites, la 4e = `fourth`
    if fourth != "pensee":
        _MISSION.add((UID1, today, "pensee"))
    if fourth != "moment":
        _MISSION.add((UID1, today, "moment"))
    if fourth != "tirage":
        _TIRAGE.add((UID1, today))
    if fourth != "consultation":
        _CONSULT.add((UID1, today))
    # exécution de la 4e action
    if fourth == "pensee":
        rj = _post("pensee").get_json()
        return rj["reward"], _ACCOUNTS[UID1]["purchased"], rj
    if fourth == "moment":
        rj = _post("moment").get_json()
        return rj["reward"], _ACCOUNTS[UID1]["purchased"], rj
    if fourth == "tirage":
        _TIRAGE.add((UID1, today))
    if fourth == "consultation":
        _CONSULT.add((UID1, today))
    rec = _reconcile(UID1)
    return ({"credited": rec["credited"], "credited_seconds": rec["credited_seconds"]},
            _ACCOUNTS[UID1]["purchased"], _get().get_json())


for _who in ("consultation", "tirage", "pensee", "moment"):
    reward, purchased, prog = _cycle30_via(_who)
    ok = (reward["credited"] is True and reward["credited_seconds"] == 900
          and purchased == 900 and prog["completed_days_total"] == 30)
    check(ok, f"9 30e journée bouclée par « {_who} » -> +900 s immédiatement, "
              f"purchased=900")

# ===========================================================================
# 10. répétition de toutes ces actions -> AUCUN double crédit
# ===========================================================================
_reset_db(); _as(UID1)
_seed_full_days(UID1, 29)
_seed_derived_day(UID1, today)
_MISSION.add((UID1, today, "pensee"))
_post("moment")                            # 30e journée -> crédit
check(_ACCOUNTS[UID1]["purchased"] == 900, "10a 1er crédit = 900")
_post("moment"); _post("pensee")
_reconcile(UID1); _reconcile(UID1)         # tirage + message rejoués
check(_ACCOUNTS[UID1]["purchased"] == 900,
      "10b rejeux (POST wellbeing + reconcile x N) -> toujours 900, aucun double crédit")
check(len([c for c in _CYCLES if c[0] == UID1]) == 1,
      "10c une seule ligne wellbeing_cycle_rewards pour le cycle 1")

# ===========================================================================
# 11. GET ne crédite JAMAIS (même à 30/30)
# ===========================================================================
_reset_db(); _as(UID1)
_seed_full_days(UID1, 30)
before = _ACCOUNTS[UID1]["purchased"]
j = _get().get_json()
check(j["completed_days_total"] == 30 and j["reward_earned_for_current_cycle"] is False,
      "11a GET voit 30 journées, reward pas encore accordée (aucun reconcile)")
check(_ACCOUNTS[UID1]["purchased"] == before and "reward" not in j,
      "11b GET n'a rien crédité et ne renvoie pas de bloc reward")

# ===========================================================================
# 12. cycle 2 : 31e journée = jour 1 du cycle 2 ; 60e = 2e crédit de 900 s
# ===========================================================================
_reset_db(); _as(UID1)
_CYCLES.add((UID1, 1))
_seed_full_days(UID1, 30)
_seed_derived_day(UID1, today)
_MISSION.add((UID1, today, "pensee"))
j = _post("moment").get_json()
check(j["completed_days_total"] == 31 and j["cycle_number"] == 2
      and j["cycle_completed_days"] == 1, "12a 31e journée -> cycle 2, jour 1")
check(j["reward"]["credited"] is False, "12b aucun crédit au jour 1 du cycle 2")

_reset_db(); _as(UID1)
_CYCLES.add((UID1, 1))
_ACCOUNTS[UID1]["purchased"] = 900
_seed_full_days(UID1, 59)
_seed_derived_day(UID1, today)
_MISSION.add((UID1, today, "pensee"))
j = _post("moment").get_json()
check(j["completed_days_total"] == 60 and j["cycle_number"] == 2
      and j["cycle_completed_days"] == 30, "12c 60e journée -> cycle 2, jour 30")
check(j["reward"] == {"credited": True, "credited_seconds": 900}
      and _ACCOUNTS[UID1]["purchased"] == 1800,
      "12d +900 s pour le cycle 2, purchased = 1800")

# ===========================================================================
# 13. niveaux 5 / 10 / 15 / 20 / 25 / 30 (par cycle) + exemple spec
# ===========================================================================
_LEVELS = [(5, "Élan", "Ancrage"), (10, "Ancrage", "Harmonie"),
           (15, "Harmonie", "Sérénité"), (20, "Sérénité", "Équilibre"),
           (25, "Équilibre", "Rayonnement"), (30, "Rayonnement", None)]
for n, cur_name, nxt_name in _LEVELS:
    _reset_db(); _as(UID1)
    _seed_full_days(UID1, n)
    j = _get().get_json()
    check(j["completed_days_total"] == n and j["current_level"] == cur_name
          and j["next_level"] == nxt_name,
          f"13 {n} journées -> niveau {cur_name}, prochain {nxt_name}")
_reset_db(); _as(UID1); _seed_full_days(UID1, 12)
j = _get().get_json()
check(j["current_level"] == "Ancrage" and j["next_level"] == "Harmonie"
      and j["days_to_next_level"] == 3, "13b exemple spec : 12 -> Ancrage, 3 avant Harmonie")

# ===========================================================================
# 14. auth obligatoire
# ===========================================================================
check(client.get("/api/app/wellbeing/progress").status_code == 401,
      "14a GET sans Bearer -> 401")
check(client.post("/api/app/wellbeing/mission",
                  json={"mission_id": "moment"}).status_code == 401,
      "14b POST sans Bearer -> 401")
_reset_db(); _ACCOUNTS[UID1]["deleted_at"] = "2026-09-10T00:00:00Z"; _as(UID1)
check(_get().status_code == 401 and _post("moment").status_code == 401,
      "14c compte supprimé -> 401")
check(_reconcile(UID1) == {"credited": False, "credited_seconds": 0,
                           "completed_days_total": 0},
      "14d _reconcile sur compte supprimé -> no-op")

# ===========================================================================
# 15. isolation utilisateur A / B
# ===========================================================================
_reset_db()
_seed_full_days(UID1, 3)
_as(UID2)
check(_get().get_json()["completed_days_total"] == 0, "15a B ne voit pas les journées de A")
_seed_full_days(UID2, 29)
_seed_derived_day(UID2, today)
_MISSION.add((UID2, today, "pensee"))
jb = _post("moment").get_json()
check(jb["reward"]["credited"] is True and _ACCOUNTS[UID2]["purchased"] == 900,
      "15b B gagne SA récompense")
check(_ACCOUNTS[UID1]["purchased"] == 0, "15c le wallet de A n'a pas bougé")

# ===========================================================================
# 16. date Europe/Paris — vrai _wellbeing_day (capturé avant monkeypatch)
# ===========================================================================
check(_REAL_WELLBEING_DAY(datetime(2026, 9, 1, 22, 30, tzinfo=timezone.utc)) == date(2026, 9, 2),
      "16a 22:30 UTC -> 2 sept à Paris (bascule minuit heure de Paris)")
check(_REAL_WELLBEING_DAY(datetime(2026, 9, 1, 21, 30, tzinfo=timezone.utc)) == date(2026, 9, 1),
      "16b 21:30 UTC -> encore le 1er sept à Paris")

# ===========================================================================
# 17. contrat du payload
# ===========================================================================
_reset_db(); _as(UID1); _seed_full_days(UID1, 12)
j = _get().get_json()
check(set(j.keys()) == {
    "completed_days_total", "cycle_completed_days", "current_level", "next_level",
    "days_to_next_level", "today", "cycle_number", "reward_earned_for_current_cycle",
}, "17a GET : clés du contrat exactes")
check(set(j["today"].keys()) == {"date", "missions", "completed"}
      and [m["id"] for m in j["today"]["missions"]]
      == ["pensee", "tirage", "consultation", "moment"],
      "17b today : date/missions/completed + 4 missions ordonnées")

# ---------------------------------------------------------------------------
print("-" * 60)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
