"""
test_app_memory_rewards.py — JEU MEMORY (« Jeu Auryel ») — GROS CHANTIER
AURYEL (Prompt 3/5) : la récompense ne crédite plus du TEMPS, elle crédite
la règle PARTAGÉE `mini_game_completed` (Étoiles), avec le MÊME plafond
quotidien que les autres mini-jeux (Suite intuitive / Carte cachée) — plus
de fenêtre glissante de 7 jours PAR DIFFICULTÉ.

Routes testées :
    POST /api/app/memory/start      { difficulty }
    POST /api/app/memory/complete   { game_id }
    GET  /api/app/memory/progress

100 % local : psycopg2 mocké, get_conn -> fausse DB en mémoire modélisant
`memory_games` / `accounts` / `reward_rules` / `reward_wallet` /
`daily_action_claims` / `reward_transactions` (le moteur Étoiles complet,
RÉUTILISÉ tel quel — `award_stars` n'est jamais mocké). La finalisation
EXACTLY-ONCE (`UPDATE ... WHERE status='active'` + rowcount) est RÉELLEMENT
appliquée par le fake, comme en base. `memory_rewards` n'est PLUS écrite
pour les nouvelles parties — un test dédié le vérifie explicitement (section
9 bis). resolve_app_session monkeypatché, _utcnow figé. Aucun réseau, aucune
DB réelle, aucun LLM.
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

DB = {
    "accounts": {},
    "memory_games": {},
    "reward_rules": {},
    "reward_wallet": {},
    "daily_action_claims": [],
    "reward_transactions": [],
}
_SQL_SEEN = []


def _default_wallet():
    return {"stars_balance": 0, "current_streak": 0, "best_streak": 0,
            "last_active_reward_date": None}


def _reset_db():
    DB["accounts"].clear()
    DB["memory_games"].clear()
    DB["reward_rules"].clear()
    DB["reward_wallet"].clear()
    DB["daily_action_claims"].clear()
    DB["reward_transactions"].clear()
    _SQL_SEEN.clear()
    DB["accounts"][UID1] = {"user_id": UID1, "deleted_at": None}
    DB["accounts"][UID2] = {"user_id": UID2, "deleted_at": None}
    # règle active telle que posée par la migration v50.
    DB["reward_rules"]["mini_game_completed"] = {
        "stars_amount": 15, "enabled": True, "daily_limit": 1,
        "cooldown_seconds": None,
    }


class _Cur:
    def __init__(self):
        self._r = None
        self.rowcount = -1

    def fetchone(self):
        return self._r

    def fetchall(self):
        return []

    def close(self):
        pass

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        _SQL_SEEN.append(s)
        p = params or ()
        self.rowcount = -1
        self._r = None

        if "FROM accounts WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE" in s:
            row = DB["accounts"].get(p[0])
            self._r = (row["user_id"],) if row and row["deleted_at"] is None else None

        elif "FROM accounts WHERE user_id=%s AND deleted_at IS NULL" in s:
            row = DB["accounts"].get(p[0])
            self._r = (row["user_id"],) if row and row["deleted_at"] is None else None

        elif "UPDATE memory_games SET status='expired', outcome='expired' " in s:
            uid, cutoff = p
            n = 0
            for gm in DB["memory_games"].values():
                if (gm["user_id"] == uid and gm["status"] == "active"
                        and gm["started_at"] < cutoff):
                    gm["status"] = "expired"
                    gm["outcome"] = "expired"
                    n += 1
            self.rowcount = n

        elif "INSERT INTO memory_games" in s:
            gid, uid, diff, started_at, created_at = p
            DB["memory_games"][gid] = {
                "user_id": uid, "difficulty": diff, "started_at": started_at,
                "status": "active", "completed_at": None,
                "elapsed_seconds": None, "stars_awarded": 0,
                "reward_credited": False, "outcome": None,
            }
            self.rowcount = 1

        elif "SELECT user_id, difficulty, started_at, status, " in s and "memory_games" in s:
            gid = p[0]
            gm = DB["memory_games"].get(gid)
            self._r = None if gm is None else (
                gm["user_id"], gm["difficulty"], gm["started_at"], gm["status"],
                gm["elapsed_seconds"], gm["stars_awarded"], gm["outcome"],
            )

        elif "UPDATE memory_games SET status='expired', completed_at=%s" in s:
            completed_at, elapsed, gid, uid = p
            gm = DB["memory_games"].get(gid)
            if gm and gm["user_id"] == uid and gm["status"] == "active":
                gm.update(status="expired", completed_at=completed_at,
                          elapsed_seconds=elapsed, outcome="expired")
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif "UPDATE memory_games SET status='completed'" in s:
            completed_at, elapsed, stars_awarded, credited, outcome, gid, uid = p
            gm = DB["memory_games"].get(gid)
            if gm and gm["user_id"] == uid and gm["status"] == "active":
                gm.update(status="completed", completed_at=completed_at,
                          elapsed_seconds=elapsed, stars_awarded=stars_awarded,
                          reward_credited=credited, outcome=outcome)
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif "INSERT INTO memory_rewards" in s:
            raise AssertionError(
                "memory_rewards ne doit plus recevoir de nouvelle ligne "
                "(Prompt 3/5 : Memory crédite désormais des Étoiles via "
                "award_stars, jamais plus temps + Étoiles à la fois)"
            )

        elif "UPDATE accounts SET earned_seconds_remaining" in s:
            raise AssertionError(
                "Memory ne doit plus créditer earned_seconds_remaining "
                "(double récompense temps + Étoiles interdite)"
            )

        elif "INSERT INTO time_ledger" in s:
            raise AssertionError(
                "Memory ne doit plus écrire dans time_ledger (plus de "
                "crédit de temps pour une nouvelle partie)"
            )

        # --- award_stars / reward_rules (moteur Étoiles, RÉUTILISÉ tel quel) ---
        elif "SELECT stars_amount, enabled, daily_limit, cooldown_seconds" in s:
            rule = DB["reward_rules"].get(p[0])
            self._r = (
                (rule["stars_amount"], rule["enabled"], rule["daily_limit"],
                 rule["cooldown_seconds"]) if rule else None
            )

        elif "SELECT stars_amount, enabled, daily_limit " in s and "reward_rules" in s:
            rule = DB["reward_rules"].get(p[0])
            self._r = (rule["stars_amount"], rule["enabled"], rule["daily_limit"]) if rule else None

        elif "SELECT MAX(created_at) FROM daily_action_claims" in s:
            uid, action_key = p
            matches = [c["created_at"] for c in DB["daily_action_claims"]
                       if c["user_id"] == uid and c["action_key"] == action_key]
            self._r = (max(matches) if matches else None,)

        elif "1 FROM daily_action_claims" in s:
            uid, claim_date = p
            self._r = (1,) if any(
                c["user_id"] == uid and c["action_key"] == "mini_game_completed"
                and c["claim_date"] == claim_date for c in DB["daily_action_claims"]
            ) else None

        elif "INSERT INTO reward_wallet (user_id, stars_balance, updated_at)" in s:
            uid = p[0]
            DB["reward_wallet"].setdefault(uid, _default_wallet())

        elif "SELECT stars_balance FROM reward_wallet WHERE user_id=%s FOR UPDATE" in s:
            uid = p[0]
            w = DB["reward_wallet"].setdefault(uid, _default_wallet())
            self._r = (w["stars_balance"],)

        elif "INSERT INTO daily_action_claims" in s:
            (cid, uid, action_key, claim_date, source_id, stars_awarded, created_at) = p
            conflict = any(
                c["user_id"] == uid and c["action_key"] == action_key
                and c["claim_date"] == claim_date
                for c in DB["daily_action_claims"]
            )
            if conflict:
                self._r = None
            else:
                DB["daily_action_claims"].append(dict(
                    id=cid, user_id=uid, action_key=action_key, claim_date=claim_date,
                    source_id=source_id, stars_awarded=stars_awarded, created_at=created_at,
                ))
                self._r = (cid,)

        elif "INSERT INTO reward_transactions" in s and "'earn'" in s:
            (tid, uid, delta, balance_after, reason, source_type, source_id,
             idem, metadata, created_at) = p
            conflict = idem is not None and any(
                t["user_id"] == uid and t.get("idempotency_key") == idem
                for t in DB["reward_transactions"]
            )
            if conflict:
                self._r = None
            else:
                DB["reward_transactions"].append(dict(
                    id=tid, user_id=uid, delta_stars=delta, balance_after=balance_after,
                    reason=reason, idempotency_key=idem, created_at=created_at,
                ))
                self._r = (tid,)

        elif "UPDATE reward_wallet SET stars_balance=%s, updated_at=%s" in s:
            new_balance, now, uid = p
            DB["reward_wallet"].setdefault(uid, _default_wallet())
            DB["reward_wallet"][uid]["stars_balance"] = new_balance

        elif ("SELECT current_streak, best_streak, last_active_reward_date "
              "FROM reward_wallet") in s:
            uid = p[0]
            w = DB["reward_wallet"].setdefault(uid, _default_wallet())
            self._r = (w["current_streak"], w["best_streak"], w["last_active_reward_date"])

        elif "UPDATE reward_wallet SET current_streak=%s" in s:
            streak, best, last_date, uid = p
            DB["reward_wallet"].setdefault(uid, _default_wallet())
            DB["reward_wallet"][uid]["current_streak"] = streak
            DB["reward_wallet"][uid]["best_streak"] = best
            DB["reward_wallet"][uid]["last_active_reward_date"] = last_date

        elif "SELECT stars_balance FROM reward_wallet WHERE user_id=%s" in s:
            uid = p[0]
            w = DB["reward_wallet"].get(uid)
            self._r = (w["stars_balance"] if w else 0,)

        else:
            raise AssertionError("SQL non géré par le fake memory : " + s)


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
    complète, puis restaure l'horloge. Renvoie (game_id, json de /complete)."""
    _as(uid)
    t0 = _NOW["t"]
    gid = _start(difficulty).get_json()["game_id"]
    _NOW["t"] = t0 + timedelta(seconds=elapsed_seconds)
    out = _complete(gid).get_json()
    _NOW["t"] = t0
    return gid, out


def _balance(uid):
    w = DB["reward_wallet"].get(uid)
    return w["stars_balance"] if w else 0


# ===========================================================================
# 0. Routes enregistrées + garde-fous serveur-autorité
# ===========================================================================
_rules = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
check(("/api/app/memory/start", "POST") in _rules, "0a POST /memory/start")
check(("/api/app/memory/complete", "POST") in _rules, "0b POST /memory/complete")
check(("/api/app/memory/progress", "GET") in _rules, "0c GET /memory/progress")

_src_complete = inspect.getsource(A.api_memory_complete)
check("data.get(\"game_id\")" in _src_complete,
      "0d /complete lit game_id dans le body")
check("(now - started_at)" in _src_complete,
      "0e /complete calcule elapsed = now - started_at (horloge serveur)")
check("award_stars" in _src_complete or "_award_stars_tx" in _src_complete,
      "0f /complete crédite via award_stars/_award_stars_tx (Étoiles, pas temps)")
check("earned_seconds_remaining" not in _src_complete,
      "0g /complete ne crédite plus earned_seconds_remaining")
check(A._MEMORY_DIFFICULTIES["easy"]["threshold_seconds"] == 20
      and A._MEMORY_DIFFICULTIES["medium"]["threshold_seconds"] == 40
      and A._MEMORY_DIFFICULTIES["hard"]["threshold_seconds"] == 80,
      "0h seuils de jeu inchangés : 20 / 40 / 80")
check("reward_seconds" not in A._MEMORY_DIFFICULTIES["easy"],
      "0i _MEMORY_DIFFICULTIES ne porte plus de reward_seconds par difficulté "
      "(récompense désormais UNIQUE, via reward_rules)")

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
# 3. start easy/medium/hard -> game_id + params (stars_reward dynamique)
# ===========================================================================
_reset_db(); _as(UID1)
for diff, seuil, pairs in (("easy", 20, 4), ("medium", 40, 6), ("hard", 80, 8)):
    j = _start(diff).get_json()
    ok = (A._is_uuid(j["game_id"]) and j["difficulty"] == diff
          and j["threshold_seconds"] == seuil and j["stars_reward"] == 15
          and j["pair_count"] == pairs and "expires_at" in j)
    check(ok, f"3 start {diff} -> game_id + seuil {seuil} + 15 ⭐ + {pairs} paires")
check(len(DB["memory_games"]) == 3, "3d 3 lignes memory_games actives créées")
check(all(g["status"] == "active" for g in DB["memory_games"].values()),
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
check(DB["memory_games"][gid_a]["status"] == "active", "4b la game de A reste active")
_as(UID1)
check(_complete("not-a-uuid").status_code == 404, "4c game_id malformé -> 404")
check(_complete("99999999-9999-4999-8999-999999999999").status_code == 404,
      "4d game_id inconnu -> 404")

# ===========================================================================
# 5. easy sous 20 s -> +15 ⭐ ; à 20 s ou au-dessus -> aucun crédit
# ===========================================================================
_reset_db()
_, out = _play(UID1, "easy", 19)
check(out["outcome"] == "rewarded" and out["reward_credited"] is True
      and out["stars_awarded"] == 15 and _balance(UID1) == 15,
      "5a easy en 19 s -> +15 ⭐")

_reset_db()
_, out = _play(UID1, "easy", 20)
check(out["outcome"] == "time_limit_exceeded" and out["reward_credited"] is False
      and out["stars_awarded"] == 0 and _balance(UID1) == 0,
      "5b easy en 20 s PILE -> aucun crédit (« moins de » strict)")

_reset_db()
_, out = _play(UID1, "easy", 45)
check(out["reward_credited"] is False and _balance(UID1) == 0,
      "5c easy en 45 s -> aucun crédit")

# ===========================================================================
# 6. medium / hard : seuils de JEU exacts inchangés, récompense TOUJOURS 15 ⭐
# ===========================================================================
_reset_db()
_, out = _play(UID1, "medium", 39)
check(out["reward_credited"] and out["stars_awarded"] == 15 and _balance(UID1) == 15,
      "6a medium 39 s -> +15 ⭐ (même montant que easy — récompense unifiée)")
_reset_db()
_, out = _play(UID1, "medium", 40)
check(not out["reward_credited"] and _balance(UID1) == 0,
      "6b medium 40 s PILE -> aucun crédit")
_reset_db()
_, out = _play(UID1, "hard", 79)
check(out["reward_credited"] and out["stars_awarded"] == 15 and _balance(UID1) == 15,
      "6c hard 79 s -> +15 ⭐ (même montant)")
_reset_db()
_, out = _play(UID1, "hard", 80)
check(not out["reward_credited"] and _balance(UID1) == 0,
      "6d hard 80 s PILE -> aucun crédit")

# ===========================================================================
# 7. jouer les 3 difficultés le MÊME jour -> UNE SEULE récompense au total
#    (plafond quotidien PARTAGÉ par la catégorie mini-jeux, pas par difficulté)
# ===========================================================================
_reset_db()
_, e = _play(UID1, "easy", 10)
_, m = _play(UID1, "medium", 10)
_, h = _play(UID1, "hard", 10)
check(e["reward_credited"] is True, "7a la 1re partie gagnée du jour est récompensée")
check(m["reward_credited"] is False and m["outcome"] == "daily_limit_reached",
      "7b la 2e partie gagnée le MÊME jour -> aucun crédit (plafond quotidien "
      "partagé, PAS un cooldown 7 j par difficulté)")
check(h["reward_credited"] is False and h["outcome"] == "daily_limit_reached",
      "7c la 3e non plus")
check(_balance(UID1) == 15,
      "7d solde = 15 (UNE seule récompense malgré 3 parties gagnées le même jour)")

# ===========================================================================
# 8. rejeu de /complete sur la même game -> aucun double crédit
# ===========================================================================
_reset_db()
gid, out1 = _play(UID1, "easy", 10)
check(_balance(UID1) == 15, "8a 1er complete -> +15 ⭐")
out2 = _complete(gid).get_json()
out3 = _complete(gid).get_json()
check(_balance(UID1) == 15
      and out2["reward_credited"] is False and out2.get("already_finalized") is True
      and out3["reward_credited"] is False,
      "8b rejeux -> toujours 15, already_finalized, aucun double crédit")

# ===========================================================================
# 9. partie déjà terminée (perdante) -> pas de crédit au rejeu
# ===========================================================================
_reset_db()
gid, out = _play(UID1, "easy", 30)  # au-dessus du seuil
check(out["outcome"] == "time_limit_exceeded", "9a partie perdante terminée")
out2 = _complete(gid).get_json()
check(out2.get("already_finalized") is True and _balance(UID1) == 0,
      "9b rejeu d'une partie perdante -> aucun crédit")

# ===========================================================================
# 9 bis. AUCUNE double récompense temps + Étoiles — vérifié EN BASE (le fake
#         lève une AssertionError si memory_rewards/earned_seconds_remaining/
#         time_ledger sont touchés — donc une simple victoire suffit à prouver
#         qu'aucun de ces chemins n'est plus emprunté).
# ===========================================================================
_reset_db()
_play(UID1, "easy", 5)
check(not any("memory_rewards" in s for s in _SQL_SEEN),
      "9c aucune requête ne touche memory_rewards pour une nouvelle partie")
check(not any("earned_seconds_remaining" in s for s in _SQL_SEEN),
      "9d aucune requête ne touche earned_seconds_remaining")
check(not any(s.startswith("INSERT INTO time_ledger") for s in _SQL_SEEN),
      "9e aucune écriture time_ledger pour cette partie")

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
      and _balance(UID1) == 0 and DB["memory_games"][gid]["status"] == "expired",
      "10a easy complétée après expiration -> expired, aucun crédit")
out2 = _complete(gid).get_json()
check(out2.get("already_finalized") is True,
      "10b rejeu d'une partie expirée -> already_finalized, aucun crédit")

# ===========================================================================
# 11. durée physiquement impossible (< plancher plausible) -> aucun crédit,
#     n'entame pas l'éligibilité du jour
# ===========================================================================
_reset_db()
gid, out = _play(UID1, "hard", 2)  # plancher hard = 9 s
check(out["outcome"] == "implausible_time" and out["reward_credited"] is False
      and _balance(UID1) == 0,
      "11a hard en 2 s -> implausible_time, aucun crédit")
# éligibilité intacte : une vraie partie juste après crédite
_, out2 = _play(UID1, "hard", 20)
check(out2["reward_credited"] is True and _balance(UID1) == 15,
      "11b partie impossible ne consomme PAS l'éligibilité -> +15 ensuite")

# ===========================================================================
# 12. plafond quotidien : lendemain -> nouvelle récompense
# ===========================================================================
_reset_db()
base = _NOW["t"]
_play(UID1, "easy", 10)
check(_balance(UID1) == 15, "12a easy gagné à J0")

_NOW["t"] = base + timedelta(hours=2)  # même jour Europe/Paris (base = 14h Paris)
gid, out = _play(UID1, "medium", 10)
check(out["outcome"] == "daily_limit_reached" and out["reward_credited"] is False
      and _balance(UID1) == 15,
      "12b même jour (autre difficulté) -> daily_limit_reached, aucun crédit")

_NOW["t"] = base + timedelta(days=1)
_, out = _play(UID1, "hard", 10)
check(out["reward_credited"] is True and _balance(UID1) == 30,
      "12c lendemain -> nouvelle récompense (+15, total 30)")
_NOW["t"] = base

# ===========================================================================
# 13. isolation stricte user A / user B (même jour, comptes séparés)
# ===========================================================================
_reset_db()
_play(UID1, "easy", 10)
_, b = _play(UID2, "easy", 10)
check(_balance(UID1) == 15 and _balance(UID2) == 15 and b["reward_credited"] is True,
      "13a A et B gagnent chacun leur récompense indépendamment")
_, a2 = _play(UID1, "medium", 10)
check(a2["reward_credited"] is False and _balance(UID1) == 15,
      "13b A reste plafonné pour aujourd'hui (indépendamment de B)")

# ===========================================================================
# 14. deux /complete « concurrents » sur la même game -> exactly once
# ===========================================================================
_reset_db(); _as(UID1)
t0 = _NOW["t"]
gid = _start("easy").get_json()["game_id"]
_NOW["t"] = t0 + timedelta(seconds=10)
first = _complete(gid).get_json()
second = _complete(gid).get_json()
_NOW["t"] = t0
check(first["reward_credited"] is True and second["reward_credited"] is False
      and _balance(UID1) == 15,
      "14 complete rejoué immédiatement -> 1 seul crédit")

# ===========================================================================
# 15. GET /progress : nouveau contrat (plafond PARTAGÉ, plus de difficulties
#     à éligibilité indépendante) + LECTURE SEULE
# ===========================================================================
_reset_db(); _as(UID1)
j = _progress().get_json()
check(set(j.keys()) == {"eligible_today", "stars_reward", "next_reset_at", "difficulties"},
      f"15a contrat exact ({set(j.keys())})")
check(j["eligible_today"] is True and j["stars_reward"] == 15
      and j["next_reset_at"] is None,
      "15b avant toute partie : éligible, 15 ⭐, pas de reset annoncé")
check([d["difficulty"] for d in j["difficulties"]] == ["easy", "medium", "hard"],
      "15c les 3 difficultés listées (paramètres de JEU, inchangés)")
_play(UID1, "easy", 10)
j2 = _progress().get_json()
check(j2["eligible_today"] is False and j2["next_reset_at"] is not None,
      "15d après une victoire du jour -> non éligible, next_reset_at renseigné")
before = _balance(UID1)
for _ in range(3):
    _progress()
check(_balance(UID1) == before,
      "15e progress répété -> aucun effet de bord, aucun crédit")

# ===========================================================================
# 16. compte supprimé -> start / complete / progress impossibles
# ===========================================================================
_reset_db(); _as(UID1)
gid = _start("easy").get_json()["game_id"]
DB["accounts"][UID1]["deleted_at"] = _NOW["t"]
check(_start("easy").status_code == 401, "16a start sur compte supprimé -> 401")
check(_complete(gid).status_code == 401, "16b complete sur compte supprimé -> 401")
check(_progress().status_code == 401, "16c progress sur compte supprimé -> 401")

# ===========================================================================
# 17. suppression de compte : tables mini-jeux dans la liste purgée
# ===========================================================================
check("memory_games" in A._ACCOUNT_DELETE_CHILD_TABLES
      and "memory_rewards" in A._ACCOUNT_DELETE_CHILD_TABLES
      and "daily_action_claims" in A._ACCOUNT_DELETE_CHILD_TABLES
      and "reward_transactions" in A._ACCOUNT_DELETE_CHILD_TABLES
      and "reward_wallet" in A._ACCOUNT_DELETE_CHILD_TABLES
      and "mini_game_sessions" in A._ACCOUNT_DELETE_CHILD_TABLES,
      "17 _ACCOUNT_DELETE_CHILD_TABLES purge toutes les tables mini-jeux/Étoiles")

# ===========================================================================
# 18. aucun moyen de choisir la récompense / elapsed via le body
# ===========================================================================
_reset_db(); _as(UID1)
t0 = _NOW["t"]
gid = _start("easy").get_json()["game_id"]
_NOW["t"] = t0 + timedelta(seconds=50)   # RÉELLEMENT au-dessus du seuil
out = _complete(gid, extra={"elapsed_seconds": 3, "stars_awarded": 99999,
                            "difficulty": "hard"}).get_json()
_NOW["t"] = t0
check(out["reward_credited"] is False and out["elapsed_seconds"] == 50
      and out["stars_awarded"] == 0 and _balance(UID1) == 0,
      "18 body { elapsed_seconds:3, stars_awarded:99999 } ignoré -> serveur "
      "calcule 50 s, seuil easy, aucun crédit")

# ===========================================================================
# 19. horloge serveur réelle (TIMESTAMPTZ)
# ===========================================================================
check(callable(_REAL_UTCNOW) and _REAL_UTCNOW().tzinfo is not None,
      "19 _utcnow réel = datetime tz-aware (horloge serveur)")

# ---------------------------------------------------------------------------
print("-" * 60)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
