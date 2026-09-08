"""
test_app_account_deletion.py — J5 : DELETE /api/app/account.

100 % local : psycopg2 mocké, get_conn -> fausse DB en mémoire modélisant
TOUTES les tables app rattachées à user_id + `accounts` + `users` (lien legacy)
+ `mobile_subscriptions`. resolve_app_session lit RÉELLEMENT la fausse table
`app_sessions` -> après suppression, l'ancien jeton ne résout plus. Aucun
réseau, aucune DB réelle, aucun LLM. Style aligné sur test_app_tirage.py.
"""

import sys
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

_CHILD = (
    "messages", "tirages", "earned_credits", "user_advisor_memory",
    "consultations", "consultation_allowance", "app_profiles",
    "share_reward_days", "wellbeing_mission_days", "wellbeing_cycle_rewards",
    "memory_games", "memory_rewards",
    "app_sessions",
)

# ---------------------------------------------------------------------------
# Fausse DB.
#   _T[table] = list of dicts each with a "user_id" key.
#   _ACCOUNTS[uid] = dict(email, email_normalized, password_hash, provider_sub,
#                         last_login_at, deleted_at)  (absent = compte hard-supprimé)
#   _USERS = list of {"phone":..., "user_id":...}
#   _SUBS  = list of {"user_id":..., "id":...}
# ---------------------------------------------------------------------------
_T = {t: [] for t in _CHILD}
_ACCOUNTS = {}
_USERS = []
_SUBS = []


def _seed(with_sub_for=None):
    for t in _CHILD:
        _T[t] = []
    _ACCOUNTS.clear()
    _USERS.clear()
    _SUBS.clear()
    for uid in (UID1, UID2):
        _ACCOUNTS[uid] = {
            "email": f"{uid[:4]}@x.co", "email_normalized": f"{uid[:4]}@x.co",
            "password_hash": "pbkdf2:sha256$x", "provider_sub": None,
            "last_login_at": "2026-09-01", "deleted_at": None,
        }
        for t in _CHILD:
            if t == "app_sessions":
                continue
            _T[t].append({"user_id": uid, "tag": f"{t}-{uid[:4]}"})
        # deux sessions actives par user (deux appareils) -> on prouve que la
        # suppression les révoque TOUTES, pas seulement celle du jeton courant.
        _T["app_sessions"].append({"user_id": uid, "token": f"tok-{uid[:4]}"})
        _T["app_sessions"].append({"user_id": uid, "token": f"tok-{uid[:4]}-b"})
    _USERS.append({"phone": "+33600000001", "user_id": UID1})  # lien legacy
    if with_sub_for:
        _SUBS.append({"user_id": with_sub_for, "id": "sub-1"})


def _count(table, uid):
    return sum(1 for r in _T[table] if r["user_id"] == uid)


class _Cur:
    def __init__(self):
        self._r = None
        self.rowcount = -1

    def fetchone(self):
        return self._r

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = " ".join(sql.split())
        p = params
        self._r = None
        self.rowcount = -1

        if k == "SELECT user_id FROM accounts WHERE user_id=%s AND deleted_at IS NULL FOR UPDATE":
            uid = p[0]
            acc = _ACCOUNTS.get(uid)
            self._r = (uid,) if (acc and acc["deleted_at"] is None) else None

        elif k.startswith("DELETE FROM ") and k.endswith(" WHERE user_id=%s"):
            table = k[len("DELETE FROM "):-len(" WHERE user_id=%s")]
            uid = p[0]
            if table == "accounts":
                _ACCOUNTS.pop(uid, None)
                self.rowcount = 1
            else:
                assert table in _T, "table inattendue dans DELETE : " + table
                n = _count(table, uid)
                _T[table] = [r for r in _T[table] if r["user_id"] != uid]
                self.rowcount = n

        elif k == "UPDATE users SET user_id=NULL WHERE user_id=%s":
            uid = p[0]
            n = 0
            for u in _USERS:
                if u["user_id"] == uid:
                    u["user_id"] = None
                    n += 1
            self.rowcount = n

        elif k == "SELECT COUNT(*) FROM mobile_subscriptions WHERE user_id=%s":
            uid = p[0]
            self._r = (sum(1 for s in _SUBS if s["user_id"] == uid),)

        elif k == ("UPDATE accounts SET email=NULL, email_normalized=NULL, "
                   "password_hash=NULL, provider_sub=NULL, last_login_at=NULL, "
                   "deleted_at=%s WHERE user_id=%s"):
            ts, uid = p
            acc = _ACCOUNTS.get(uid)
            if acc:
                acc.update(email=None, email_normalized=None, password_hash=None,
                           provider_sub=None, last_login_at=None, deleted_at=ts)
                self.rowcount = 1
            else:
                self.rowcount = 0

        else:
            raise AssertionError("SQL non géré par le fake deletion : " + k)


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


# resolve_app_session RÉEL contre la fausse table app_sessions + accounts :
# le jeton "tok-<uid4>" résout tant que sa ligne app_sessions existe ET que le
# compte n'est pas supprimé/anonymisé.
def _fake_resolve(token):
    if not token:
        return None
    row = next((s for s in _T["app_sessions"] if s.get("token") == token), None)
    if row is None:
        return None
    acc = _ACCOUNTS.get(row["user_id"])
    if acc is None or acc["deleted_at"] is not None:
        return None
    return {"session_id": "s", "user_id": row["user_id"], "email": acc["email"]}


A.resolve_app_session = _fake_resolve


def _hdr(uid):
    return {"Authorization": f"Bearer tok-{uid[:4]}"}


def _delete(uid):
    return client.delete("/api/app/account", headers=_hdr(uid))


# ===========================================================================
# 0. Route existe au chemin EXACT, méthode DELETE
# ===========================================================================
_rules = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
check(("/api/app/account", "DELETE") in _rules, "0 DELETE /api/app/account enregistrée")

# ===========================================================================
# 1. DELETE sans auth -> refus
# ===========================================================================
_seed()
check(client.delete("/api/app/account").status_code == 401, "1a DELETE sans Bearer -> 401")
check(client.delete("/api/app/account",
                    headers={"Authorization": "Bearer inconnu"}).status_code == 401,
      "1b DELETE jeton inconnu -> 401")

# ===========================================================================
# 2-8. DELETE compte valide (sans abonnement) -> hard delete + purge enfants
# ===========================================================================
_seed()
_sessions_before = _count("app_sessions", UID1)
r = _delete(UID1)
check(r.status_code == 200 and r.get_json() == {"status": "deleted"},
      "2 DELETE compte valide -> 200 {status: deleted}")
check(UID1 not in _ACCOUNTS, "3 accounts : ligne réellement supprimée (hard delete, sans abonnement)")
check(_count("messages", UID1) == 0, "4 messages supprimés")
check(_count("consultations", UID1) == 0, "5 consultations supprimées")
check(_count("consultation_allowance", UID1) == 0, "5b consultation_allowance supprimée")
check(_count("tirages", UID1) == 0, "6 tirages supprimés")
check(_count("user_advisor_memory", UID1) == 0, "7 mémoire conseiller supprimée")
check(_count("app_profiles", UID1) == 0, "3b profil (app_profiles) supprimé")
check(_count("earned_credits", UID1) == 0, "3c earned_credits supprimés")
check(_count("share_reward_days", UID1) == 0, "3d share_reward_days supprimés")
check(_count("memory_games", UID1) == 0 and _count("memory_rewards", UID1) == 0,
      "3e memory_games + memory_rewards supprimés")
check(_sessions_before == 2 and _count("app_sessions", UID1) == 0,
      "8 sessions supprimées/révoquées (les 2 appareils, pas seulement le jeton courant)")
check(next(u["user_id"] for u in _USERS if u["phone"] == "+33600000001") is None,
      "8b lien legacy users.user_id détaché (NULL), dossier phone non supprimé")

# ===========================================================================
# 9. ancien token inutilisable après succès
# ===========================================================================
check(_fake_resolve("tok-1111") is None, "9a resolve_app_session(ancien jeton) -> None")
check(_delete(UID1).status_code == 401, "9b réutiliser l'ancien jeton -> 401")

# ===========================================================================
# 10. l'ancien compte ne restaure plus les données
# ===========================================================================
check(UID1 not in _ACCOUNTS and all(_count(t, UID1) == 0 for t in _CHILD),
      "10a aucune donnée résiduelle pour l'ancien user_id")
_free_email = not any(a.get("email_normalized") == "1111@x.co" for a in _ACCOUNTS.values())
check(_free_email, "10b email libéré : une réinscription créerait un compte NEUF (user_id neuf)")

# ===========================================================================
# 11. mobile_subscriptions : comportement documenté + testé
# ===========================================================================
# 11a — AVEC preuve d'achat -> anonymisation, compte conservé, sub conservée.
_seed(with_sub_for=UID1)
r = _delete(UID1)
check(r.status_code == 200, "11a DELETE compte AVEC abonnement -> 200")
check(UID1 in _ACCOUNTS, "11b accounts : ligne CONSERVÉE (anonymisée) tant qu'une preuve d'achat existe")
acc = _ACCOUNTS[UID1]
check(acc["email"] is None and acc["email_normalized"] is None
      and acc["password_hash"] is None and acc["provider_sub"] is None
      and acc["last_login_at"] is None,
      "11c PII du compte effacée (email/email_normalized/password_hash/provider_sub/last_login_at = NULL)")
check(acc["deleted_at"] is not None, "11d deleted_at posé (login impossible, jeton rejeté)")
check(sum(1 for s in _SUBS if s["user_id"] == UID1) == 1,
      "11e mobile_subscriptions CONSERVÉE (obligation comptable/fiscale/fraude/litige)")
check(all(_count(t, UID1) == 0 for t in _CHILD),
      "11f toutes les autres données personnelles supprimées même dans le cas anonymisation")
check(_fake_resolve("tok-1111") is None, "11g jeton inutilisable (compte anonymisé)")

# 11b — SANS preuve d'achat -> hard delete (déjà couvert en 2-3), re-vérifié ici.
_seed(with_sub_for=None)
_delete(UID1)
check(UID1 not in _ACCOUNTS, "11h sans abonnement -> hard delete réel du compte")

# ===========================================================================
# 12. la suppression ne touche JAMAIS un autre user
# ===========================================================================
_seed(with_sub_for=UID1)
_delete(UID1)
check(UID2 in _ACCOUNTS and _ACCOUNTS[UID2]["email"] == "2222@x.co",
      "12a compte UID2 intact")
check(all(_count(t, UID2) == 1 for t in _CHILD if t != "app_sessions")
      and _count("app_sessions", UID2) == 2,
      "12b toutes les données + sessions de UID2 intactes")
check(sum(1 for s in _SUBS if s["user_id"] == UID2) == 0
      and _fake_resolve("tok-2222") is not None,
      "12c abonnement/jeton de UID2 non affectés")

# ===========================================================================
# Idempotence : DELETE d'un compte déjà supprimé -> 200 (contrat 2xx)
# ===========================================================================
_seed()
_ACCOUNTS[UID1]["deleted_at"] = A._utcnow()          # déjà anonymisé
# la ligne app_sessions existe encore mais deleted_at != NULL -> resolve None
check(_delete(UID1).status_code == 401,
      "idem-a jeton d'un compte déjà supprimé -> 401 (require_app_auth)")

# ===========================================================================
print("-" * 64)
print(f"{_STATE['pass']}/{_STATE['pass'] + _STATE['fail']} tests passés")
sys.exit(1 if _STATE["fail"] else 0)
