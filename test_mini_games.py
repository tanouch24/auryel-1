"""
test_mini_games.py — GROS CHANTIER AURYEL (Prompt 3/5) : session serveur
générique de mini-jeux (`start_mini_game` / `finish_mini_game` +
POST /api/app/minigame/start /finish), RÉUTILISÉE par « Suite intuitive »
(sequence_recall) et « Carte cachée » (hidden_card). Même récompense
PARTAGÉE `mini_game_completed` que Memory (award_stars, plafond quotidien
commun à toute la catégorie).

100 % local : psycopg2 mocké, FakeConn/FakeCursor en mémoire. Aucune vraie
DB, aucun réseau, aucun LLM.
"""

import sys
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


NOW = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)
A._utcnow = lambda: NOW

UID = "11111111-1111-4111-8111-111111111111"
UID2 = "22222222-2222-4222-8222-222222222222"

DB = {
    "accounts": {},
    "mini_game_sessions": {},
    "reward_rules": {},
    "reward_wallet": {},
    "daily_action_claims": [],
    "reward_transactions": [],
}


def _default_wallet():
    return {"stars_balance": 0, "current_streak": 0, "best_streak": 0,
            "last_active_reward_date": None}


def reset_db():
    DB["accounts"].clear()
    DB["mini_game_sessions"].clear()
    DB["reward_rules"].clear()
    DB["reward_wallet"].clear()
    DB["daily_action_claims"].clear()
    DB["reward_transactions"].clear()
    DB["accounts"][UID] = {"deleted_at": None}
    DB["accounts"][UID2] = {"deleted_at": None}
    DB["reward_rules"]["mini_game_completed"] = {
        "stars_amount": 15, "enabled": True, "daily_limit": 1, "cooldown_seconds": None,
    }


class FakeCursor:
    def __init__(self):
        self._r = None
        self.rowcount = -1

    def fetchone(self):
        return self._r

    def close(self):
        pass

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        p = params or ()
        self.rowcount = -1
        self._r = None

        if "FROM accounts WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE" in s:
            acc = DB["accounts"].get(p[0])
            self._r = (p[0],) if acc and acc["deleted_at"] is None else None

        elif "UPDATE mini_game_sessions SET status='expired' WHERE user_id=%s" in s:
            uid, cutoff = p
            for sess in DB["mini_game_sessions"].values():
                if (sess["user_id"] == uid and sess["status"] == "active"
                        and sess["started_at"] < cutoff):
                    sess["status"] = "expired"

        elif "INSERT INTO mini_game_sessions" in s:
            sid, uid, game_key, started_at, created_at = p
            DB["mini_game_sessions"][sid] = {
                "user_id": uid, "game_key": game_key, "started_at": started_at,
                "status": "active", "completed_at": None,
            }

        elif "SELECT user_id, game_key, started_at, status" in s:
            sid = p[0]
            sess = DB["mini_game_sessions"].get(sid)
            self._r = None if sess is None else (
                sess["user_id"], sess["game_key"], sess["started_at"], sess["status"],
            )

        elif "UPDATE mini_game_sessions SET status='expired', completed_at=%s" in s:
            now, sid, uid = p
            sess = DB["mini_game_sessions"].get(sid)
            if sess and sess["user_id"] == uid and sess["status"] == "active":
                sess.update(status="expired", completed_at=now)
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif "UPDATE mini_game_sessions SET status='completed', completed_at=%s" in s:
            now, sid, uid = p
            sess = DB["mini_game_sessions"].get(sid)
            if sess and sess["user_id"] == uid and sess["status"] == "active":
                sess.update(status="completed", completed_at=now)
                self.rowcount = 1
            else:
                self.rowcount = 0

        # --- moteur Étoiles (award_stars), RÉUTILISÉ tel quel ---
        elif "SELECT stars_amount, enabled, daily_limit, cooldown_seconds" in s:
            rule = DB["reward_rules"].get(p[0])
            self._r = (
                (rule["stars_amount"], rule["enabled"], rule["daily_limit"],
                 rule["cooldown_seconds"]) if rule else None
            )

        elif "SELECT MAX(created_at) FROM daily_action_claims" in s:
            uid, action_key = p
            matches = [c["created_at"] for c in DB["daily_action_claims"]
                       if c["user_id"] == uid and c["action_key"] == action_key]
            self._r = (max(matches) if matches else None,)

        elif "INSERT INTO reward_wallet (user_id, stars_balance, updated_at)" in s:
            DB["reward_wallet"].setdefault(p[0], _default_wallet())

        elif "SELECT stars_balance FROM reward_wallet WHERE user_id=%s FOR UPDATE" in s:
            w = DB["reward_wallet"].setdefault(p[0], _default_wallet())
            self._r = (w["stars_balance"],)

        elif "INSERT INTO daily_action_claims" in s:
            (cid, uid, action_key, claim_date, source_id, stars_awarded, created_at) = p
            conflict = any(
                c["user_id"] == uid and c["action_key"] == action_key
                and c["claim_date"] == claim_date for c in DB["daily_action_claims"]
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
            w = DB["reward_wallet"].setdefault(p[0], _default_wallet())
            self._r = (w["current_streak"], w["best_streak"], w["last_active_reward_date"])

        elif "UPDATE reward_wallet SET current_streak=%s" in s:
            streak, best, last_date, uid = p
            DB["reward_wallet"].setdefault(uid, _default_wallet())
            DB["reward_wallet"][uid]["current_streak"] = streak
            DB["reward_wallet"][uid]["best_streak"] = best
            DB["reward_wallet"][uid]["last_active_reward_date"] = last_date

        elif "SELECT stars_balance FROM reward_wallet WHERE user_id=%s" in s:
            w = DB["reward_wallet"].get(p[0])
            self._r = (w["stars_balance"] if w else 0,)

        else:
            raise AssertionError("SQL non modélisé (mini_games) : " + s)


class FakeConn:
    def cursor(self):
        return FakeCursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


A.get_conn = lambda: FakeConn()
A.app.config["TESTING"] = True
try:
    A.limiter.enabled = False
except Exception:
    pass
client = A.app.test_client()
_CURRENT = {"uid": UID}
A.resolve_app_session = lambda tok: (
    {"session_id": "s", "user_id": _CURRENT["uid"], "email": "x@x.co"} if tok else None
)


def _as(uid):
    _CURRENT["uid"] = uid


def _hdr(tok="x"):
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _balance(uid):
    w = DB["reward_wallet"].get(uid)
    return w["stars_balance"] if w else 0


# ---------------------------------------------------------------------------
# 1. start_mini_game — fonction directe
# ---------------------------------------------------------------------------
print("=" * 64)
print("1. start_mini_game")
print("=" * 64)

reset_db()
r = A.start_mini_game(UID, "sequence_recall", now=NOW)
check(A._is_uuid(r["session_id"]), "1a session_id imprévisible (uuid4)")
check(r["game_key"] == "sequence_recall", "1b game_key renvoyé")
check("expires_at" in r, "1c expires_at présent")

r_bad = A.start_mini_game(UID, "not_a_game", now=NOW)
check(r_bad.get("error") == "invalid_game", "1d game_key invalide -> error")

# ---------------------------------------------------------------------------
# 2. finish_mini_game — completion valide = +15 ⭐
# ---------------------------------------------------------------------------
print("=" * 64)
print("2. Completion valide")
print("=" * 64)

reset_db()
s = A.start_mini_game(UID, "hidden_card", now=NOW)
later = NOW + timedelta(seconds=5)  # > min_plausible_seconds (2s pour hidden_card)
res = A.finish_mini_game(UID, s["session_id"], now=later)
check(res["status"] == "completed" and res["awarded"] is True
      and res["stars_awarded"] == 15, "2a completion valide -> +15 ⭐")
check(_balance(UID) == 15, "2b wallet réellement crédité")

# ---------------------------------------------------------------------------
# 3. Abandon (jamais fini) = 0 — pas de finish appelé, rien ne se passe.
# ---------------------------------------------------------------------------
print("=" * 64)
print("3. Abandon")
print("=" * 64)

reset_db()
A.start_mini_game(UID, "sequence_recall", now=NOW)
check(_balance(UID) == 0, "3 aucun finish appelé -> aucune récompense")

# ---------------------------------------------------------------------------
# 4. Double finish -> une seule récompense
# ---------------------------------------------------------------------------
print("=" * 64)
print("4. Double finish")
print("=" * 64)

reset_db()
s = A.start_mini_game(UID, "sequence_recall", now=NOW)
later = NOW + timedelta(seconds=4)
r1 = A.finish_mini_game(UID, s["session_id"], now=later)
r2 = A.finish_mini_game(UID, s["session_id"], now=later)
check(r1["awarded"] is True and r2["awarded"] is False
      and r2["outcome"] == "already_finalized",
      "4a 2e finish -> already_finalized, aucun 2e crédit")
check(_balance(UID) == 15, "4b solde inchangé après le rejeu")

# ---------------------------------------------------------------------------
# 5. Replay d'une session finalisée après un NOUVEAU jour -> toujours 0
#    (la session elle-même ne peut être finalisée qu'une fois, peu importe le
#    jour — c'est daily_action_claims qui gère le "par jour", pas la session)
# ---------------------------------------------------------------------------
print("=" * 64)
print("5. Replay session (jour suivant)")
print("=" * 64)

lendemain = NOW + timedelta(days=1)
r3 = A.finish_mini_game(UID, s["session_id"], now=lendemain)
check(r3["awarded"] is False and r3["outcome"] == "already_finalized",
      "5 rejeu de la MÊME session, même après un jour -> toujours "
      "already_finalized (une session = une seule tentative, à vie)")

# ---------------------------------------------------------------------------
# 6. Règle désactivée -> 0
# ---------------------------------------------------------------------------
print("=" * 64)
print("6. Règle désactivée")
print("=" * 64)

reset_db()
DB["reward_rules"]["mini_game_completed"]["enabled"] = False
s = A.start_mini_game(UID, "hidden_card", now=NOW)
r = A.finish_mini_game(UID, s["session_id"], now=NOW + timedelta(seconds=5))
check(r["awarded"] is False and r["outcome"] == "rule_disabled",
      "6 règle désactivée -> aucune récompense")

# ---------------------------------------------------------------------------
# 7. Session expirée -> aucune récompense
# ---------------------------------------------------------------------------
print("=" * 64)
print("7. Session expirée")
print("=" * 64)

reset_db()
s = A.start_mini_game(UID, "hidden_card", now=NOW)
much_later = NOW + timedelta(seconds=A._MINI_GAMES["hidden_card"]["expiry_seconds"] + 5)
r = A.finish_mini_game(UID, s["session_id"], now=much_later)
check(r["status"] == "expired" and r["awarded"] is False,
      "7 session expirée -> status=expired, aucune récompense")

# ---------------------------------------------------------------------------
# 8. Durée implausible (start -> finish instantané) -> aucune récompense
# ---------------------------------------------------------------------------
print("=" * 64)
print("8. Durée implausible")
print("=" * 64)

reset_db()
s = A.start_mini_game(UID, "sequence_recall", now=NOW)
r = A.finish_mini_game(UID, s["session_id"], now=NOW)  # instantané
check(r["awarded"] is False and r["outcome"] == "implausible_time",
      "8 finish quasi-instantané -> implausible_time, aucun crédit")

# ---------------------------------------------------------------------------
# 9. Session inconnue / d'un autre utilisateur -> not_found
# ---------------------------------------------------------------------------
print("=" * 64)
print("9. Sessions invalides")
print("=" * 64)

reset_db()
r = A.finish_mini_game(UID, "99999999-9999-4999-8999-999999999999", now=NOW)
check(r["status"] == "not_found", "9a session inconnue -> not_found")

s = A.start_mini_game(UID, "sequence_recall", now=NOW)
r = A.finish_mini_game(UID2, s["session_id"], now=NOW + timedelta(seconds=5))
check(r["status"] == "not_found",
      "9b session d'un AUTRE utilisateur -> not_found (jamais un accès croisé)")

# ---------------------------------------------------------------------------
# 10. Le plafond quotidien est PARTAGÉ avec Memory (même règle, même clé de
#     jour) — vérifié directement via daily_action_claims.
# ---------------------------------------------------------------------------
print("=" * 64)
print("10. Plafond PARTAGÉ avec Memory (award_stars mini_game_completed)")
print("=" * 64)

reset_db()
s = A.start_mini_game(UID, "sequence_recall", now=NOW)
A.finish_mini_game(UID, s["session_id"], now=NOW + timedelta(seconds=5))
check(_balance(UID) == 15, "10a 1er mini-jeu du jour -> +15")
s2 = A.start_mini_game(UID, "hidden_card", now=NOW)
r2 = A.finish_mini_game(UID, s2["session_id"], now=NOW + timedelta(seconds=5))
check(r2["awarded"] is False and _balance(UID) == 15,
      "10b un DEUXIÈME mini-jeu différent le même jour -> aucun 2e crédit "
      "(daily_action_claims scopé par rule_key, pas par jeu)")

# ---------------------------------------------------------------------------
# 11. HTTP — routes enregistrées + auth + contrat
# ---------------------------------------------------------------------------
print("=" * 64)
print("11. HTTP /api/app/minigame/*")
print("=" * 64)

_rules = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
check(("/api/app/minigame/start", "POST") in _rules, "11a route start enregistrée")
check(("/api/app/minigame/finish", "POST") in _rules, "11b route finish enregistrée")

reset_db(); _as(UID)
check(client.post("/api/app/minigame/start", json={"game_key": "hidden_card"},
                  headers=_hdr(None)).status_code == 401, "11c start sans Bearer -> 401")
r = client.post("/api/app/minigame/start", json={"game_key": "hidden_card"}, headers=_hdr())
check(r.status_code == 200, "11d start authentifié -> 200")
sid = r.get_json()["session_id"]
check(client.post("/api/app/minigame/start", json={"game_key": "n_importe_quoi"},
                  headers=_hdr()).status_code == 400, "11e game_key invalide -> 400")

r2 = client.post("/api/app/minigame/finish", json={"session_id": sid}, headers=_hdr())
check(r2.status_code == 200, "11f finish authentifié -> 200")
check(client.post("/api/app/minigame/finish", json={"session_id": "not-a-uuid"},
                  headers=_hdr()).status_code == 404, "11g session_id malformé -> 404")
check(client.post("/api/app/minigame/finish",
                  json={"session_id": "99999999-9999-4999-8999-999999999999"},
                  headers=_hdr()).status_code == 404, "11h session inconnue -> 404")

# ---------------------------------------------------------------------------
# 12. Signature — aucun paramètre de montant/récompense
# ---------------------------------------------------------------------------
print("=" * 64)
print("12. Sécurité — signatures sans montant")
print("=" * 64)

import inspect as _inspect
for fn in (A.start_mini_game, A.finish_mini_game):
    params = set(_inspect.signature(fn).parameters)
    check(not ({"stars_amount", "reward", "amount"} & params),
          f"12 {fn.__name__}() n'accepte aucun paramètre de récompense "
          f"(paramètres : {sorted(params)})")

print("-" * 64)
total = _STATE["pass"] + _STATE["fail"]
print(f"RESULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko (sur {total})")
sys.exit(1 if _STATE["fail"] else 0)
