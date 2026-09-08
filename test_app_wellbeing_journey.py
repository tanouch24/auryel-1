"""
test_app_wellbeing_journey.py — J7 : « Mon parcours bien-être ».

Routes testées :
    GET  /api/app/wellbeing/progress
    POST /api/app/wellbeing/mission

100 % local : psycopg2 mocké, get_conn -> fausse DB en mémoire modélisant
share_reward_days / tirages / consultations / wellbeing_mission_days /
wellbeing_cycle_rewards / accounts (purchased_seconds_remaining). Les 3
missions dérivées (pensee / tirage / consultation) sont alimentées via leurs
traces ; `moment` via l'endpoint. resolve_app_session monkeypatché,
_wellbeing_day figé. Aucun réseau, aucune DB réelle, aucun LLM. Style aligné
sur test_app_rewards_share.py.
"""

import sys
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
#   _PENSEE   : {(uid, date)}   -> share_reward_days
#   _TIRAGE   : {(uid, date)}   -> tirages (date Europe/Paris de created_at)
#   _CONSULT  : {(uid, date)}   -> consultations (date Europe/Paris de last_activity_at)
#   _MOMENT   : {(uid, date, "moment")} -> wellbeing_mission_days
#   _CYCLES   : {(uid, cycle_number)}   -> wellbeing_cycle_rewards
#   _ACCOUNTS : uid -> {"purchased": int, "deleted_at": None|ts}
# ---------------------------------------------------------------------------
_PENSEE = set()
_TIRAGE = set()
_CONSULT = set()
_MOMENT = set()
_CYCLES = set()
_ACCOUNTS = {}


def _reset_db():
    _PENSEE.clear(); _TIRAGE.clear(); _CONSULT.clear()
    _MOMENT.clear(); _CYCLES.clear(); _ACCOUNTS.clear()
    _ACCOUNTS[UID1] = {"purchased": 0, "deleted_at": None}
    _ACCOUNTS[UID2] = {"purchased": 0, "deleted_at": None}


def _seed_derived_day(uid, d):
    """Marque les 3 missions à trace serveur (pensee/tirage/consultation) faites
    le jour `d` pour `uid`."""
    _PENSEE.add((uid, d)); _TIRAGE.add((uid, d)); _CONSULT.add((uid, d))


def _seed_full_days(uid, days):
    """`days` journées ENTIÈREMENT complétées, dates décroissantes à partir
    d'hier (pour ne pas empiéter sur 'aujourd'hui' figé)."""
    base = _FAKE_TODAY["d"] - timedelta(days=1)
    for i in range(days):
        d = base - timedelta(days=i)
        _seed_derived_day(uid, d)
        _MOMENT.add((uid, d, "moment"))


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

        elif k == "SELECT share_date FROM share_reward_days WHERE user_id=%s":
            uid = p[0]
            self._rows = [(d,) for (u, d) in _PENSEE if u == uid]

        elif k == ("SELECT (created_at AT TIME ZONE 'Europe/Paris')::date "
                   "FROM tirages WHERE user_id=%s"):
            uid = p[0]
            self._rows = [(d,) for (u, d) in _TIRAGE if u == uid]

        elif k == ("SELECT (last_activity_at AT TIME ZONE 'Europe/Paris')::date "
                   "FROM consultations WHERE user_id=%s AND last_activity_at IS NOT NULL"):
            uid = p[0]
            self._rows = [(d,) for (u, d) in _CONSULT if u == uid]

        elif k == ("SELECT day_date FROM wellbeing_mission_days "
                   "WHERE user_id=%s AND mission_id=%s"):
            uid, mid = p
            self._rows = [(d,) for (u, d, m) in _MOMENT if u == uid and m == mid]

        elif k == "SELECT cycle_number FROM wellbeing_cycle_rewards WHERE user_id=%s":
            uid = p[0]
            self._rows = [(c,) for (u, c) in _CYCLES if u == uid]

        elif k == ("INSERT INTO wellbeing_mission_days (user_id, day_date, mission_id, created_at) "
                   "VALUES (%s, %s, %s, %s) "
                   "ON CONFLICT (user_id, day_date, mission_id) DO NOTHING"):
            uid, d, mid, _ts = p
            key = (uid, d, mid)
            if key in _MOMENT:
                self.rowcount = 0
            else:
                _MOMENT.add(key)
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
_REAL_WELLBEING_DAY = A._wellbeing_day          # gardé pour le test date Europe/Paris
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


# ===========================================================================
# 0. Routes aux chemins EXACTS
# ===========================================================================
_rules = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
check(("/api/app/wellbeing/progress", "GET") in _rules,
      "0a GET /api/app/wellbeing/progress enregistrée")
check(("/api/app/wellbeing/mission", "POST") in _rules,
      "0b POST /api/app/wellbeing/mission enregistrée")

# ===========================================================================
# 1. Progression vide
# ===========================================================================
_reset_db(); _as(UID1)
j = _get().get_json()
check(j["completed_days_total"] == 0, "1a total = 0")
check(j["cycle_completed_days"] == 0, "1b cycle_completed_days = 0")
check(j["current_level"] is None, "1c current_level null")
check(j["next_level"] == "Élan" and j["days_to_next_level"] == 5, "1d next = Élan (5)")
check(j["cycle_number"] == 1, "1e cycle 1")
check(j["reward_earned_for_current_cycle"] is False, "1f reward pas gagnée")
check(j["today"]["date"] == "2026-09-15", "1g today.date Europe/Paris")
check([m["id"] for m in j["today"]["missions"]] == ["pensee", "tirage", "consultation", "moment"],
      "1h 4 missions dans l'ordre")
check(all(m["completed"] is False for m in j["today"]["missions"]), "1i aucune mission faite")
check(j["today"]["completed"] is False, "1j journée non complétée")

# ===========================================================================
# 2. 1 mission sur 4 (moment seul) -> aucune journée
# ===========================================================================
_reset_db(); _as(UID1)
j = _post("moment").get_json()
check(j["today"]["missions"][3] == {"id": "moment", "completed": True}, "2a moment fait")
check(j["today"]["completed"] is False, "2b 1/4 -> journée non complétée")
check(j["completed_days_total"] == 0, "2c total encore 0")
check(j["reward"] == {"credited": False, "credited_seconds": 0}, "2d aucun crédit")

# ===========================================================================
# 3. mission dérivée sans trace -> 409 mission_action_missing
# ===========================================================================
_reset_db(); _as(UID1)
r = _post("tirage")
check(r.status_code == 409, "3a POST tirage sans trace -> 409")
check(r.get_json().get("error") == "mission_action_missing", "3b erreur mission_action_missing")
_TIRAGE.add((UID1, _FAKE_TODAY["d"]))
r2 = _post("tirage")
check(r2.status_code == 200, "3c avec la trace du jour -> 200")
check(r2.get_json()["today"]["missions"][1]["completed"] is True, "3d tirage marqué fait")

# ===========================================================================
# 4. mission_id invalide -> 400
# ===========================================================================
_reset_db(); _as(UID1)
r = _post("meditation-plus")
check(r.status_code == 400 and r.get_json().get("error") == "invalid_mission",
      "4 mission_id inconnu -> 400 invalid_mission")

# ===========================================================================
# 5. 4 missions sur 4 le même jour = +1 journée complétée
# ===========================================================================
_reset_db(); _as(UID1)
today = _FAKE_TODAY["d"]
_seed_derived_day(UID1, today)                      # pensee + tirage + consultation
j = _post("moment").get_json()                      # 4e mission
check(all(m["completed"] for m in j["today"]["missions"]), "5a 4/4 missions faites")
check(j["today"]["completed"] is True, "5b journée du jour complétée")
check(j["completed_days_total"] == 1, "5c +1 journée complétée")
check(j["cycle_completed_days"] == 1 and j["cycle_number"] == 1, "5d cycle 1, jour 1")
check(j["reward"]["credited"] is False, "5e pas de récompense au jour 1")

# ===========================================================================
# 6. répéter la même mission le même jour = idempotent (pas de 2e journée)
# ===========================================================================
j2 = _post("moment").get_json()
check(j2["completed_days_total"] == 1, "6a moment rejoué -> toujours 1 journée")
j3 = _post("pensee").get_json()
check(j3["completed_days_total"] == 1, "6b pensee (déjà là) -> toujours 1 journée")
check(j3["reward"]["credited"] is False, "6c aucun crédit sur rejeu")

# ===========================================================================
# 7. journées NON consécutives : un jour manqué ne remet rien à zéro
# ===========================================================================
_reset_db(); _as(UID1)
d1 = today - timedelta(days=5)
d2 = today - timedelta(days=2)
for d in (d1, d2):
    _seed_derived_day(UID1, d)
    _MOMENT.add((UID1, d, "moment"))
j = _get().get_json()
check(j["completed_days_total"] == 2, "7a 2 journées complétées non consécutives")
check(j["cycle_completed_days"] == 2, "7b progression conservée (pas de reset)")

# ===========================================================================
# 8. niveaux 5 / 10 / 15 / 20 / 25 / 30
# ===========================================================================
_LEVELS = [(5, "Élan", "Ancrage"), (10, "Ancrage", "Harmonie"),
           (15, "Harmonie", "Sérénité"), (20, "Sérénité", "Équilibre"),
           (25, "Équilibre", "Rayonnement"), (30, "Rayonnement", None)]
for n, cur_name, nxt_name in _LEVELS:
    _reset_db(); _as(UID1)
    _seed_full_days(UID1, n)
    j = _get().get_json()
    ok = (j["completed_days_total"] == n
          and j["current_level"] == cur_name
          and j["next_level"] == nxt_name)
    check(ok, f"8 {n} journées -> niveau {cur_name}, prochain {nxt_name}")
# exemple spec : 12 journées -> Ancrage, plus que 3 avant Harmonie
_reset_db(); _as(UID1); _seed_full_days(UID1, 12)
j = _get().get_json()
check(j["current_level"] == "Ancrage" and j["next_level"] == "Harmonie"
      and j["days_to_next_level"] == 3, "8b exemple spec : 12 -> Ancrage, 3 avant Harmonie")

# ===========================================================================
# 9. 30 journées complétées = +900 s + niveau Rayonnement, UNE fois
# ===========================================================================
_reset_db(); _as(UID1)
_seed_full_days(UID1, 29)                # 29 journées déjà complétées
_seed_derived_day(UID1, today)          # 3 missions du jour
j = _post("moment").get_json()          # 30e journée bouclée par la 4e mission
check(j["completed_days_total"] == 30, "9a 30 journées complétées")
check(j["cycle_completed_days"] == 30 and j["cycle_number"] == 1, "9b cycle 1, jour 30")
check(j["current_level"] == "Rayonnement", "9c niveau Rayonnement atteint")
check(j["reward"] == {"credited": True, "credited_seconds": 900}, "9d +900 s créditées CETTE requête")
check(j["reward_earned_for_current_cycle"] is True, "9e reward_earned_for_current_cycle = true")
check(_ACCOUNTS[UID1]["purchased"] == 900, "9f purchased_seconds_remaining += 900")

# ===========================================================================
# 10. rejouer la requête du jour 30 = AUCUN 2e crédit (idempotent)
# ===========================================================================
j2 = _post("moment").get_json()
check(j2["reward"] == {"credited": False, "credited_seconds": 0}, "10a rejeu jour 30 -> pas de 2e crédit")
j3 = _post("pensee").get_json()
check(j3["reward"]["credited"] is False, "10b autre mission rejouée -> pas de 2e crédit")
check(_ACCOUNTS[UID1]["purchased"] == 900, "10c purchased reste à 900 (un seul crédit)")

# ===========================================================================
# 11. GET ne crédite JAMAIS
# ===========================================================================
_reset_db(); _as(UID1)
_seed_full_days(UID1, 30)
before = _ACCOUNTS[UID1]["purchased"]
j = _get().get_json()
check(j["completed_days_total"] == 30 and j["reward_earned_for_current_cycle"] is False,
      "11a GET voit 30 journées mais reward pas encore accordée (aucun POST)")
check(_ACCOUNTS[UID1]["purchased"] == before, "11b GET n'a rien crédité")
check("reward" not in j, "11c GET ne renvoie pas de bloc reward")

# ===========================================================================
# 12. cycle 2 : 31e journée = jour 1 du cycle 2
# ===========================================================================
_reset_db(); _as(UID1)
_CYCLES.add((UID1, 1))                   # cycle 1 déjà récompensé
_seed_full_days(UID1, 30)
_seed_derived_day(UID1, today)
j = _post("moment").get_json()
check(j["completed_days_total"] == 31, "12a 31 journées complétées")
check(j["cycle_number"] == 2 and j["cycle_completed_days"] == 1, "12b cycle 2, jour 1")
check(j["current_level"] is None and j["next_level"] == "Élan", "12c niveau remis à zéro dans le cycle 2")
check(j["reward"]["credited"] is False, "12d aucune récompense au jour 1 du cycle 2")
check(j["reward_earned_for_current_cycle"] is False, "12e cycle 2 pas encore récompensé")

# ===========================================================================
# 13. cycle 2 bouclé = 2e récompense de 900 s (une par cycle)
# ===========================================================================
_reset_db(); _as(UID1)
_CYCLES.add((UID1, 1))
_ACCOUNTS[UID1]["purchased"] = 900       # crédit du cycle 1
_seed_full_days(UID1, 59)
_seed_derived_day(UID1, today)
j = _post("moment").get_json()
check(j["completed_days_total"] == 60, "13a 60 journées complétées")
check(j["cycle_number"] == 2 and j["cycle_completed_days"] == 30, "13b cycle 2, jour 30")
check(j["reward"] == {"credited": True, "credited_seconds": 900}, "13c +900 s pour le cycle 2")
check(_ACCOUNTS[UID1]["purchased"] == 1800, "13d purchased = 1800 (2 cycles x 900)")

# ===========================================================================
# 14. auth obligatoire
# ===========================================================================
r = client.get("/api/app/wellbeing/progress")
check(r.status_code == 401, "14a GET sans Bearer -> 401")
r = client.post("/api/app/wellbeing/mission", json={"mission_id": "moment"})
check(r.status_code == 401, "14b POST sans Bearer -> 401")
_reset_db()
_ACCOUNTS[UID1]["deleted_at"] = "2026-09-10T00:00:00Z"
_as(UID1)
check(_get().status_code == 401, "14c compte supprimé -> GET 401")
check(_post("moment").status_code == 401, "14d compte supprimé -> POST 401")

# ===========================================================================
# 15. isolation utilisateur A / B
# ===========================================================================
_reset_db()
_seed_full_days(UID1, 3)
_as(UID2)
j = _get().get_json()
check(j["completed_days_total"] == 0, "15a B ne voit pas les journées de A")
_as(UID1)
check(_get().get_json()["completed_days_total"] == 3, "15b A voit bien ses 3 journées")
# B crédité indépendamment
_as(UID2)
_seed_full_days(UID2, 29)
_seed_derived_day(UID2, today)
jb = _post("moment").get_json()
check(jb["reward"]["credited"] is True and _ACCOUNTS[UID2]["purchased"] == 900, "15c B gagne sa propre récompense")
check(_ACCOUNTS[UID1]["purchased"] == 0, "15d le wallet de A n'a pas bougé")

# ===========================================================================
# 16. date Europe/Paris — teste le VRAI _wellbeing_day (capturé avant monkeypatch)
# ===========================================================================
# 22:30 UTC le 1er sept = 00:30 le 2 sept à Paris (CEST +2) -> bascule minuit Paris.
check(_REAL_WELLBEING_DAY(datetime(2026, 9, 1, 22, 30, tzinfo=timezone.utc)) == date(2026, 9, 2),
      "16a 22:30 UTC -> 2 sept à Paris (bascule minuit heure de Paris)")
check(_REAL_WELLBEING_DAY(datetime(2026, 9, 1, 21, 30, tzinfo=timezone.utc)) == date(2026, 9, 1),
      "16b 21:30 UTC -> encore le 1er sept à Paris")
# datetime naïf traité comme UTC (même règle que _reward_share_date)
check(_REAL_WELLBEING_DAY(datetime(2026, 1, 15, 23, 30)) == date(2026, 1, 16),
      "16c naïf 23:30 (UTC) en janvier -> 16 janv à Paris (CET +1)")

# ===========================================================================
# 17. non-régression : payload GET == contrat conceptuel de la spec
# ===========================================================================
_reset_db(); _as(UID1); _seed_full_days(UID1, 12)
j = _get().get_json()
expected_keys = {
    "completed_days_total", "cycle_completed_days", "current_level", "next_level",
    "days_to_next_level", "today", "cycle_number", "reward_earned_for_current_cycle",
}
check(set(j.keys()) == expected_keys, "17a GET : clés du contrat exactes")
check(set(j["today"].keys()) == {"date", "missions", "completed"}, "17b today : date/missions/completed")
check(all(set(m.keys()) == {"id", "completed"} for m in j["today"]["missions"]),
      "17c mission : id/completed")

# ---------------------------------------------------------------------------
print("-" * 60)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
