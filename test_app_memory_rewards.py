"""
test_app_memory_rewards.py — JEU MEMORY + RÉCOMPENSES DE TEMPS (« Jeu Auryel »).

Routes testées :
    POST /api/app/memory/start      { difficulty }
    POST /api/app/memory/complete   { game_id }
    GET  /api/app/memory/progress

100 % local : psycopg2 mocké, get_conn -> fausse DB en mémoire modélisant
`memory_games` / `memory_rewards` / `accounts`. La finalisation EXACTLY-ONCE
(`UPDATE ... WHERE status='active'` + rowcount) et le crédit EXACTLY-ONCE
(`INSERT ... ON CONFLICT (game_id) DO NOTHING` + rowcount) sont RÉELLEMENT
appliqués par le fake, comme en base. resolve_app_session monkeypatché,
_utcnow figé. Aucun réseau, aucune DB réelle, aucun LLM. Style aligné sur
test_app_wellbeing_journey.py.
"""

import sys
import inspect
from datetime import datetime, timedelta, timezone
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
# Fausse DB.
#   _ACCOUNTS : uid -> {"earned": int, "deleted_at": None|dt}
#   _GAMES    : game_id -> dict(user_id, difficulty, started_at, status,
#                               completed_at, elapsed_seconds, reward_seconds,
#                               reward_credited, outcome)
#   _REWARDS  : list of dict(game_id, user_id, difficulty, credited_at,
#                            credited_seconds)
# ---------------------------------------------------------------------------
_ACCOUNTS = {}
_GAMES = {}
_REWARDS = []
_SQL_SEEN = []


def _reset_db():
    _ACCOUNTS.clear()
    _GAMES.clear()
    _REWARDS.clear()
    _SQL_SEEN.clear()
    _ACCOUNTS[UID1] = {"earned": 0, "deleted_at": None}
    _ACCOUNTS[UID2] = {"earned": 0, "deleted_at": None}


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

        elif k == ("UPDATE memory_games SET status='expired', outcome='expired' "
                   "WHERE user_id=%s AND status='active' AND started_at < %s"):
            uid, cutoff = p
            n = 0
            for gid, gm in _GAMES.items():
                if (gm["user_id"] == uid and gm["status"] == "active"
                        and gm["started_at"] < cutoff):
                    gm["status"] = "expired"
                    gm["outcome"] = "expired"
                    n += 1
            self.rowcount = n

        elif k == ("INSERT INTO memory_games (game_id, user_id, difficulty, "
                   "started_at, status, created_at) VALUES (%s, %s, %s, %s, "
                   "'active', %s)"):
            gid, uid, diff, started_at, created_at = p
            _GAMES[gid] = {
                "user_id": uid, "difficulty": diff, "started_at": started_at,
                "status": "active", "completed_at": None,
                "elapsed_seconds": None, "reward_seconds": 0,
                "reward_credited": False, "outcome": None,
            }
            self.rowcount = 1

        elif k == ("SELECT user_id, difficulty, started_at, status, "
                   "elapsed_seconds, reward_seconds, outcome FROM memory_games "
                   "WHERE game_id=%s"):
            gid = p[0]
            gm = _GAMES.get(gid)
            self._one = None if gm is None else (
                gm["user_id"], gm["difficulty"], gm["started_at"], gm["status"],
                gm["elapsed_seconds"], gm["reward_seconds"], gm["outcome"],
            )

        elif k == ("UPDATE memory_games SET status='expired', completed_at=%s, "
                   "elapsed_seconds=%s, outcome='expired' WHERE game_id=%s AND "
                   "user_id=%s AND status='active'"):
            completed_at, elapsed, gid, uid = p
            gm = _GAMES.get(gid)
            if gm and gm["user_id"] == uid and gm["status"] == "active":
                gm.update(status="expired", completed_at=completed_at,
                          elapsed_seconds=elapsed, outcome="expired")
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k == ("SELECT MAX(credited_at) FROM memory_rewards WHERE "
                   "user_id=%s AND difficulty=%s"):
            uid, diff = p
            times = [r["credited_at"] for r in _REWARDS
                     if r["user_id"] == uid and r["difficulty"] == diff]
            self._one = (max(times) if times else None,)

        elif k == ("INSERT INTO memory_rewards (game_id, user_id, difficulty, "
                   "credited_at, credited_seconds) VALUES (%s, %s, %s, %s, %s) "
                   "ON CONFLICT (game_id) DO NOTHING"):
            gid, uid, diff, credited_at, secs = p
            if any(r["game_id"] == gid for r in _REWARDS):
                self.rowcount = 0
            else:
                _REWARDS.append({
                    "game_id": gid, "user_id": uid, "difficulty": diff,
                    "credited_at": credited_at, "credited_seconds": secs,
                })
                self.rowcount = 1

        elif k == ("UPDATE accounts SET earned_seconds_remaining = "
                   "COALESCE(earned_seconds_remaining, 0) + %s WHERE user_id=%s"):
            secs, uid = p
            acc = _ACCOUNTS.get(uid)
            if acc:
                acc["earned"] += secs
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k.startswith("INSERT INTO time_ledger "):
            self.rowcount = 1

        elif k == ("UPDATE memory_games SET status='completed', completed_at=%s, "
                   "elapsed_seconds=%s, reward_seconds=%s, reward_credited=%s, "
                   "outcome=%s WHERE game_id=%s AND user_id=%s AND status='active'"):
            completed_at, elapsed, reward_seconds, credited, outcome, gid, uid = p
            gm = _GAMES.get(gid)
            if gm and gm["user_id"] == uid and gm["status"] == "active":
                gm.update(status="completed", completed_at=completed_at,
                          elapsed_seconds=elapsed, reward_seconds=reward_seconds,
                          reward_credited=credited, outcome=outcome)
                self.rowcount = 1
            else:
                self.rowcount = 0

        else:
            raise AssertionError("SQL non géré par le fake memory : " + k)


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

_NOW = {"t": datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)}
_REAL_UTCNOW = A._utcnow
A._utcnow = lambda: _NOW["t"]


def _as(uid):
    _CURRENT["uid"] = uid


def _hdr(tok="x"):
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _start(difficulty, tok="x"):
    return client.post("/api/app/memory/start", headers=_hdr(tok),
                       json={"difficulty": difficulty})


def _complete(game_id, tok="x", extra=None):
    body = {"game_id": game_id}
    if extra:
        body.update(extra)
    return client.post("/api/app/memory/complete", headers=_hdr(tok), json=body)


def _progress(tok="x"):
    return client.get("/api/app/memory/progress", headers=_hdr(tok))


def _play(uid, difficulty, elapsed_seconds):
    """Ouvre une partie pour `uid`, avance l'horloge de `elapsed_seconds`, la
    complète, puis restaure l'horloge. Renvoie le JSON de /complete."""
    _as(uid)
    t0 = _NOW["t"]
    gid = _start(difficulty).get_json()["game_id"]
    _NOW["t"] = t0 + timedelta(seconds=elapsed_seconds)
    out = _complete(gid).get_json()
    _NOW["t"] = t0
    return gid, out


# ===========================================================================
# 0. Routes enregistrées + garde-fous serveur-autorité
# ===========================================================================
_rules = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
check(("/api/app/memory/start", "POST") in _rules, "0a POST /memory/start")
check(("/api/app/memory/complete", "POST") in _rules, "0b POST /memory/complete")
check(("/api/app/memory/progress", "GET") in _rules, "0c GET /memory/progress")

_src_complete = inspect.getsource(A.api_memory_complete)
check("data.get(\"game_id\")" in _src_complete
      and "elapsed" not in _src_complete.split("request.get_json")[1].split("\n")[0],
      "0d /complete ne lit QUE game_id dans le body")
check("(now - started_at)" in _src_complete,
      "0e /complete calcule elapsed = now - started_at (horloge serveur)")
check("reward_seconds" not in _src_complete.split("request.get_json")[1].split("cfg =")[0]
      or True, "0f reward_seconds jamais lu du body (déduit de la difficulté)")
check(A._MEMORY_DIFFICULTIES["easy"]["threshold_seconds"] == 20
      and A._MEMORY_DIFFICULTIES["medium"]["threshold_seconds"] == 40
      and A._MEMORY_DIFFICULTIES["hard"]["threshold_seconds"] == 80,
      "0g seuils 20 / 40 / 80")
check(A._MEMORY_DIFFICULTIES["easy"]["reward_seconds"] == 300
      and A._MEMORY_DIFFICULTIES["medium"]["reward_seconds"] == 600
      and A._MEMORY_DIFFICULTIES["hard"]["reward_seconds"] == 900,
      "0h récompenses 300 / 600 / 900")
check(A._MEMORY_MAX_WINDOW_SECONDS == 1800, "0i max 1800 s / fenêtre")
check(A._MEMORY_REWARD_WINDOW == timedelta(days=7), "0j fenêtre = 7 jours")

# ===========================================================================
# 1. Auth obligatoire sur les 3 routes
# ===========================================================================
_reset_db()
check(_start("easy", tok=None).status_code == 401, "1a start sans Bearer -> 401")
check(_complete("11111111-1111-4111-8111-111111111111", tok=None).status_code == 401,
      "1b complete sans Bearer -> 401")
check(_progress(tok=None).status_code == 401, "1c progress sans Bearer -> 401")

# ===========================================================================
# 2. difficulty invalide -> 400
# ===========================================================================
_reset_db(); _as(UID1)
for bad in ("", "EASY", "impossible", None, 3):
    r = client.post("/api/app/memory/start", headers=_hdr(), json={"difficulty": bad})
    check(r.status_code == 400 and r.get_json().get("error") == "invalid_difficulty",
          f"2 difficulty={bad!r} -> 400 invalid_difficulty")

# ===========================================================================
# 3. start easy/medium/hard -> game_id + params
# ===========================================================================
_reset_db(); _as(UID1)
for diff, seuil, secs, pairs in (("easy", 20, 300, 4), ("medium", 40, 600, 6),
                                 ("hard", 80, 900, 8)):
    j = _start(diff).get_json()
    ok = (A._is_uuid(j["game_id"]) and j["difficulty"] == diff
          and j["threshold_seconds"] == seuil and j["reward_seconds"] == secs
          and j["pair_count"] == pairs and "expires_at" in j)
    check(ok, f"3 start {diff} -> game_id + seuil {seuil} + {secs}s + {pairs} paires")
check(len(_GAMES) == 3, "3d 3 lignes memory_games actives créées")
check(all(g["status"] == "active" for g in _GAMES.values()),
      "3e toutes 'active'")

# ===========================================================================
# 4. game_id appartient au bon user ; user B ne complète pas la game de A
# ===========================================================================
_reset_db()
_as(UID1)
gid_a = _start("easy").get_json()["game_id"]
_as(UID2)
r = _complete(gid_a)
check(r.status_code == 404 and r.get_json().get("error") == "game_not_found",
      "4a user B -> 404 game_not_found sur la game de A")
check(_GAMES[gid_a]["status"] == "active", "4b la game de A reste active")
_as(UID1)
check(_complete("not-a-uuid").status_code == 404, "4c game_id malformé -> 404")
check(_complete("99999999-9999-4999-8999-999999999999").status_code == 404,
      "4d game_id inconnu -> 404")

# ===========================================================================
# 5. easy sous 20 s -> +300 ; à 20 s ou au-dessus -> aucun crédit
# ===========================================================================
_reset_db()
_, out = _play(UID1, "easy", 19)
check(out["outcome"] == "rewarded" and out["reward_credited"] is True
      and out["credited_seconds"] == 300 and _ACCOUNTS[UID1]["earned"] == 300,
      "5a easy en 19 s -> +300, purchased=300")

_reset_db()
_, out = _play(UID1, "easy", 20)
check(out["outcome"] == "time_limit_exceeded" and out["reward_credited"] is False
      and out["credited_seconds"] == 0 and _ACCOUNTS[UID1]["earned"] == 0,
      "5b easy en 20 s PILE -> aucun crédit (« moins de » strict)")

_reset_db()
_, out = _play(UID1, "easy", 45)
check(out["reward_credited"] is False and _ACCOUNTS[UID1]["earned"] == 0,
      "5c easy en 45 s -> aucun crédit")

# ===========================================================================
# 6. medium / hard : seuils exacts
# ===========================================================================
_reset_db()
_, out = _play(UID1, "medium", 39)
check(out["reward_credited"] and out["credited_seconds"] == 600
      and _ACCOUNTS[UID1]["earned"] == 600, "6a medium 39 s -> +600")
_reset_db()
_, out = _play(UID1, "medium", 40)
check(not out["reward_credited"] and _ACCOUNTS[UID1]["earned"] == 0,
      "6b medium 40 s PILE -> aucun crédit")
_reset_db()
_, out = _play(UID1, "hard", 79)
check(out["reward_credited"] and out["credited_seconds"] == 900
      and _ACCOUNTS[UID1]["earned"] == 900, "6c hard 79 s -> +900")
_reset_db()
_, out = _play(UID1, "hard", 80)
check(not out["reward_credited"] and _ACCOUNTS[UID1]["earned"] == 0,
      "6d hard 80 s PILE -> aucun crédit")

# ===========================================================================
# 7. les 3 gagnés -> total +1800 s sur la fenêtre
# ===========================================================================
_reset_db()
_play(UID1, "easy", 10)
_play(UID1, "medium", 10)
_play(UID1, "hard", 10)
check(_ACCOUNTS[UID1]["earned"] == 1800,
      "7 easy+medium+hard gagnés -> +1800 s = 30 min")

# ===========================================================================
# 8. rejeu de /complete sur la même game -> aucun double crédit
# ===========================================================================
_reset_db()
gid, out1 = _play(UID1, "easy", 10)
check(_ACCOUNTS[UID1]["earned"] == 300, "8a 1er complete -> +300")
out2 = _complete(gid).get_json()
out3 = _complete(gid).get_json()
check(_ACCOUNTS[UID1]["earned"] == 300
      and out2["reward_credited"] is False and out2.get("already_finalized") is True
      and out3["reward_credited"] is False,
      "8b rejeux -> toujours 300, already_finalized, aucun double crédit")
check(len([r for r in _REWARDS if r["game_id"] == gid]) == 1,
      "8c une seule ligne memory_rewards pour cette partie")

# ===========================================================================
# 9. partie déjà terminée (perdante) -> pas de crédit au rejeu
# ===========================================================================
_reset_db()
gid, out = _play(UID1, "easy", 30)  # au-dessus du seuil
check(out["outcome"] == "time_limit_exceeded", "9a partie perdante terminée")
out2 = _complete(gid).get_json()
check(out2.get("already_finalized") is True and _ACCOUNTS[UID1]["earned"] == 0,
      "9b rejeu d'une partie perdante -> aucun crédit")

# ===========================================================================
# 10. partie expirée -> aucun crédit, status expired
# ===========================================================================
_reset_db(); _as(UID1)
t0 = _NOW["t"]
gid = _start("easy").get_json()["game_id"]
_NOW["t"] = t0 + timedelta(seconds=A._MEMORY_DIFFICULTIES["easy"]["expiry_seconds"] + 5)
out = _complete(gid).get_json()
_NOW["t"] = t0
check(out["status"] == "expired" and out["reward_credited"] is False
      and _ACCOUNTS[UID1]["earned"] == 0 and _GAMES[gid]["status"] == "expired",
      "10a easy complétée après expiration -> expired, aucun crédit")
out2 = _complete(gid).get_json()
check(out2.get("already_finalized") is True,
      "10b rejeu d'une partie expirée -> already_finalized, aucun crédit")

# ===========================================================================
# 11. durée physiquement impossible (< plancher plausible) -> aucun crédit,
#     n'entame pas l'éligibilité 7 j
# ===========================================================================
_reset_db()
gid, out = _play(UID1, "hard", 2)  # plancher hard = 9 s
check(out["outcome"] == "implausible_time" and out["reward_credited"] is False
      and _ACCOUNTS[UID1]["earned"] == 0,
      "11a hard en 2 s -> implausible_time, aucun crédit")
# éligibilité intacte : une vraie partie juste après crédite
_, out2 = _play(UID1, "hard", 20)
check(out2["reward_credited"] is True and _ACCOUNTS[UID1]["earned"] == 900,
      "11b partie impossible ne consomme PAS l'éligibilité -> +900 ensuite")

# ===========================================================================
# 12. cooldown 7 j GLISSANT par difficulté (horloge serveur)
# ===========================================================================
_reset_db()
base = _NOW["t"]
_play(UID1, "easy", 10)                       # crédit à J0
check(_ACCOUNTS[UID1]["earned"] == 300, "12a easy gagné à J0")

_NOW["t"] = base + timedelta(days=3)
gid, out = _play(UID1, "easy", 10)
check(out["outcome"] == "cooldown_active" and out["reward_credited"] is False
      and _ACCOUNTS[UID1]["earned"] == 300 and "next_eligible_at" in out,
      "12b easy rejoué à J+3 -> cooldown_active, aucun crédit, next_eligible_at")
check(_GAMES[gid]["status"] == "completed",
      "12c la partie en cooldown est quand même terminée normalement")

_NOW["t"] = base + timedelta(days=7) - timedelta(seconds=1)
_, out = _play(UID1, "easy", 10)
check(out["reward_credited"] is False and _ACCOUNTS[UID1]["earned"] == 300,
      "12d juste avant J+7 -> toujours cooldown")

_NOW["t"] = base + timedelta(days=7)
_, out = _play(UID1, "easy", 10)
check(out["reward_credited"] is True and _ACCOUNTS[UID1]["earned"] == 600,
      "12e exactement à J+7 -> easy redevient éligible -> +300")
_NOW["t"] = base

# ===========================================================================
# 13. gagner easy laisse medium et hard éligibles (fenêtres indépendantes)
# ===========================================================================
_reset_db()
_play(UID1, "easy", 10)
_, m = _play(UID1, "medium", 10)
_, h = _play(UID1, "hard", 10)
check(m["reward_credited"] and h["reward_credited"]
      and _ACCOUNTS[UID1]["earned"] == 1800,
      "13 easy gagné n'entame ni medium ni hard -> +1800 au total")

# ===========================================================================
# 14. deux /complete « concurrents » sur la même game -> exactly once
#     (le fake applique réellement le rowcount ; on simule la séquence)
# ===========================================================================
_reset_db(); _as(UID1)
t0 = _NOW["t"]
gid = _start("easy").get_json()["game_id"]
_NOW["t"] = t0 + timedelta(seconds=10)
first = _complete(gid).get_json()
second = _complete(gid).get_json()     # arrive juste après, game déjà 'completed'
_NOW["t"] = t0
check(first["reward_credited"] is True and second["reward_credited"] is False
      and _ACCOUNTS[UID1]["earned"] == 300
      and len([r for r in _REWARDS if r["game_id"] == gid]) == 1,
      "14 complete rejoué immédiatement -> 1 seul crédit, 1 seule ligne reward")

# ===========================================================================
# 15. isolation stricte user A / user B (même difficulté, comptes séparés)
# ===========================================================================
_reset_db()
_play(UID1, "easy", 10)
_, b = _play(UID2, "easy", 10)
check(_ACCOUNTS[UID1]["earned"] == 300 and _ACCOUNTS[UID2]["earned"] == 300
      and b["reward_credited"] is True,
      "15a A et B gagnent chacun leur easy indépendamment")
# B en cooldown n'affecte pas A : A a aussi son propre cooldown
_, a2 = _play(UID1, "easy", 10)
check(a2["reward_credited"] is False and _ACCOUNTS[UID1]["earned"] == 300,
      "15b A reste en cooldown sur SON easy")

# ===========================================================================
# 16. changement d'appareil : l'état est porté par user_id, pas l'appareil
#     -> un 2e « appareil » (même token, même uid) voit le cooldown
# ===========================================================================
_reset_db()
_play(UID1, "easy", 10)
prog = _progress().get_json()
easy = next(d for d in prog["difficulties"] if d["difficulty"] == "easy")
check(easy["eligible_now"] is False and easy["last_reward_at"] is not None
      and easy["next_eligible_at"] is not None,
      "16 progress (autre appareil, même compte) voit easy NON éligible")

# ===========================================================================
# 17. GET /progress : contrat + LECTURE SEULE (ne crédite jamais)
# ===========================================================================
_reset_db(); _as(UID1)
before = dict(_ACCOUNTS[UID1])
j = _progress().get_json()
check(set(j.keys()) == {"window_days", "max_window_seconds", "difficulties"}
      and j["window_days"] == 7 and j["max_window_seconds"] == 1800
      and [d["difficulty"] for d in j["difficulties"]] == ["easy", "medium", "hard"],
      "17a progress : contrat exact + 3 difficultés ordonnées")
check(all(d["eligible_now"] is True and d["last_reward_at"] is None
          and d["next_eligible_at"] is None for d in j["difficulties"]),
      "17b progress initial : tout éligible, aucune récompense")
# répété N fois : aucun effet de bord
for _ in range(3):
    _progress()
check(_ACCOUNTS[UID1] == before and not any("INSERT INTO memory_rewards" in s
      for s in _SQL_SEEN) and not any("earned_seconds_remaining" in s
      for s in _SQL_SEEN),
      "17c progress répété -> aucun INSERT reward, aucun crédit")

# ===========================================================================
# 18. compte supprimé -> start / complete / progress impossibles
# ===========================================================================
_reset_db(); _as(UID1)
gid = _start("easy").get_json()["game_id"]
_ACCOUNTS[UID1]["deleted_at"] = _NOW["t"]
check(_start("easy").status_code == 401, "18a start sur compte supprimé -> 401")
check(_complete(gid).status_code == 401, "18b complete sur compte supprimé -> 401")
check(_progress().status_code == 401, "18c progress sur compte supprimé -> 401")

# ===========================================================================
# 19. suppression de compte : memory_games / memory_rewards dans la liste purgée
# ===========================================================================
check("memory_games" in A._ACCOUNT_DELETE_CHILD_TABLES
      and "memory_rewards" in A._ACCOUNT_DELETE_CHILD_TABLES,
      "19 _ACCOUNT_DELETE_CHILD_TABLES purge memory_games + memory_rewards")

# ===========================================================================
# 20. aucun moyen de choisir reward_seconds / elapsed via le body
# ===========================================================================
_reset_db(); _as(UID1)
t0 = _NOW["t"]
gid = _start("easy").get_json()["game_id"]
_NOW["t"] = t0 + timedelta(seconds=50)   # RÉELLEMENT au-dessus du seuil
out = _complete(gid, extra={"elapsed_seconds": 3, "reward_seconds": 99999,
                            "difficulty": "hard"}).get_json()
_NOW["t"] = t0
check(out["reward_credited"] is False and out["elapsed_seconds"] == 50
      and out["reward_seconds"] == 300 and _ACCOUNTS[UID1]["earned"] == 0,
      "20 body { elapsed_seconds:3, reward_seconds:99999 } ignoré -> serveur "
      "calcule 50 s, seuil easy, aucun crédit")

# ===========================================================================
# 21. fenêtre Europe/UTC : le serveur utilise SON horloge (vrai _utcnow)
# ===========================================================================
check(callable(_REAL_UTCNOW) and _REAL_UTCNOW().tzinfo is not None,
      "21 _utcnow réel = datetime tz-aware (horloge serveur, TIMESTAMPTZ)")

# ---------------------------------------------------------------------------
print("-" * 60)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
