"""
test_app_profile.py — B3.1 : profil app (app_profiles) + messages par user_id.

100 % local : psycopg2 mocké, get_conn remplacé par une fausse connexion en
mémoire, aucun réseau, aucune DB Railway. Style aligné sur test_auth.py.
"""

import sys
from unittest.mock import MagicMock, patch

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

import inspect
import re

import auryel_bot as A

_STATE = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _STATE["pass"] += 1
        print(f"✅ {label}")
    else:
        _STATE["fail"] += 1
        print(f"❌ {label}")


# ---------------------------------------------------------------------------
# Fausse base de données en mémoire
# ---------------------------------------------------------------------------
FAKE = {"accounts": [], "app_profiles": [], "messages": [], "users": [], "seq": [0]}


def reset_db():
    for key in ("accounts", "app_profiles", "messages", "users"):
        FAKE[key].clear()
    FAKE["seq"][0] = 0


def seed_account(user_id, deleted_at=None, email="u@example.com"):
    FAKE["accounts"].append({"user_id": user_id, "email": email, "deleted_at": deleted_at})


_APP_SELECT = "SELECT " + ", ".join(A._APP_PROFILE_FIELDS) + " FROM app_profiles WHERE user_id=%s"
_APP_INSERT = ("INSERT INTO app_profiles (user_id, guide, onboarding_done, date_premier_contact, "
               "date_dernier_contact) VALUES (%s, 'selena', FALSE, %s, %s) ON CONFLICT (user_id) DO NOTHING")


def _blank_profile(uid):
    row = {f: "" for f in A._APP_PROFILE_FIELDS}
    row["user_id"] = uid
    row["guide"] = "selena"
    for intcol in ("nb_echanges", "nb_echanges_decouverte", "nb_echanges_dernier_tirage",
                   "nb_echanges_dernier_psaume"):
        row[intcol] = 0
    row["tirage_propose_en_attente"] = False
    row["onboarding_done"] = False
    return row


class FakeCursor:
    def __init__(self):
        self._result = None
        self._rows = None
        self.rowcount = -1

    def fetchone(self):
        return self._result

    def fetchall(self):
        return self._rows or []

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = " ".join(sql.split())
        p = params
        self.rowcount = -1

        if k == "SELECT deleted_at FROM accounts WHERE user_id=%s":
            (uid,) = p
            r = next((a for a in FAKE["accounts"] if a["user_id"] == uid), None)
            self._result = (r["deleted_at"],) if r else None

        elif k == _APP_SELECT:
            (uid,) = p
            r = next((x for x in FAKE["app_profiles"] if x["user_id"] == uid), None)
            self._result = tuple(r[f] for f in A._APP_PROFILE_FIELDS) if r else None

        elif k == _APP_INSERT:
            uid, dpc, ddc = p
            if not any(x["user_id"] == uid for x in FAKE["app_profiles"]):
                row = _blank_profile(uid)
                row["date_premier_contact"] = dpc
                row["date_dernier_contact"] = ddc
                FAKE["app_profiles"].append(row)

        elif k.startswith("UPDATE app_profiles SET ") and k.endswith(" WHERE user_id=%s"):
            set_part = k[len("UPDATE app_profiles SET "):-len(" WHERE user_id=%s")]
            cols = [seg.split("=")[0].strip() for seg in set_part.split(",")]
            uid = p[-1]
            values = list(p[:-1])
            for r in FAKE["app_profiles"]:
                if r["user_id"] == uid:
                    for col, val in zip(cols, values):
                        r[col] = val

        elif k == ("INSERT INTO messages (user_id, phone, role, content, timestamp, consultation_id) "
                   "VALUES (%s, NULL, %s, %s, %s, %s)"):
            uid, role, content, ts, consultation_id = p
            FAKE["seq"][0] += 1
            FAKE["messages"].append({"id": FAKE["seq"][0], "user_id": uid, "phone": None,
                                     "role": role, "content": content, "timestamp": ts,
                                     "consultation_id": consultation_id})

        elif k == "SELECT role,content FROM messages WHERE user_id=%s ORDER BY id DESC LIMIT %s":
            uid, limit = p
            rows = [m for m in FAKE["messages"] if m["user_id"] == uid]
            rows.sort(key=lambda m: m["id"], reverse=True)
            self._rows = [(m["role"], m["content"]) for m in rows[:limit]]

        elif k == "SELECT role,content,timestamp FROM messages WHERE user_id=%s ORDER BY id ASC":
            (uid,) = p
            rows = sorted([m for m in FAKE["messages"] if m["user_id"] == uid], key=lambda m: m["id"])
            self._rows = [(m["role"], m["content"], m["timestamp"]) for m in rows]

        # ---- legacy (régression) ----
        elif k == "INSERT INTO messages (phone,role,content,timestamp,meta_message_id) VALUES (%s,%s,%s,%s,%s)":
            phone, role, content, ts, meta = p
            FAKE["seq"][0] += 1
            FAKE["messages"].append({"id": FAKE["seq"][0], "user_id": None, "phone": phone,
                                     "role": role, "content": content, "timestamp": ts,
                                     "meta_message_id": meta})

        elif k == "SELECT role,content FROM messages WHERE phone=%s ORDER BY id DESC LIMIT %s":
            phone, limit = p
            rows = [m for m in FAKE["messages"] if m.get("phone") == phone]
            rows.sort(key=lambda m: m["id"], reverse=True)
            self._rows = [(m["role"], m["content"]) for m in rows[:limit]]

        else:
            raise AssertionError("SQL non géré par le fake : " + k)


class FakeConn:
    def cursor(self):
        return FakeCursor()

    def commit(self):
        pass

    def close(self):
        pass


A.get_conn = lambda: FakeConn()

UID1 = "11111111-1111-4111-8111-111111111111"
UID2 = "22222222-2222-4222-8222-222222222222"

print("=" * 60)
print("TEST APP PROFILE — B3.1")
print("=" * 60)

# 1. création profil pour account valide
reset_db()
seed_account(UID1)
prof = A.get_or_create_app_profile(UID1)
check(isinstance(prof, dict) and prof["user_id"] == UID1 and len(FAKE["app_profiles"]) == 1,
      "1 profil créé pour un account valide")

# 2. second appel -> même profil
prof2 = A.get_or_create_app_profile(UID1)
check(prof2 == prof and len(FAKE["app_profiles"]) == 1,
      "2 second appel -> même profil, pas de doublon")

# 3. account inexistant refusé
reset_db()
check(A.get_or_create_app_profile("00000000-0000-4000-8000-000000000000") is None
      and len(FAKE["app_profiles"]) == 0,
      "3 account inexistant -> None, aucun profil")

# 4. account deleted_at refusé
reset_db()
seed_account(UID1, deleted_at="2026-01-01T00:00:00")
check(A.get_or_create_app_profile(UID1) is None and len(FAKE["app_profiles"]) == 0,
      "4 account soft-supprimé -> None, aucun profil")

# 5. aucun phone créé
reset_db()
seed_account(UID1)
A.get_or_create_app_profile(UID1)
check("phone" not in FAKE["app_profiles"][0] and len(FAKE["users"]) == 0,
      "5 aucun phone, aucune ligne users créée")

# 6. aucun appel à create_user()
reset_db()
seed_account(UID1)
with patch.object(A, "create_user") as m_cu:
    A.get_or_create_app_profile(UID1)
check(m_cu.call_count == 0, "6 create_user() jamais appelé")

# 7. guide par défaut = selena
reset_db()
seed_account(UID1)
prof = A.get_or_create_app_profile(UID1)
check(prof["guide"] == "selena", "7 guide par défaut = selena")

# 8. update guide
A.update_app_profile(UID1, guide="maia")
check(A.get_app_profile(UID1)["guide"] == "maia", "8 update_app_profile(guide) fonctionne")

# 9. update prenom
A.update_app_profile(UID1, prenom="Sarah")
check(A.get_app_profile(UID1)["prenom"] == "Sarah", "9 update_app_profile(prenom) fonctionne")

# 10. colonne non whitelistée refusée
_raised = 0
for bad in ({"phone": "x"}, {"stripe_customer_id": "y"}, {"abonne": True}):
    try:
        A.update_app_profile(UID1, **bad)
    except ValueError:
        _raised += 1
check(_raised == 3, "10 colonnes hors whitelist -> ValueError (phone / stripe / abonne)")

# 11. _app_profile_to_user_dict contient exactement les clés de get_user()
# (profil neuf : les tests 8/9 avaient muté guide/prenom)
reset_db()
seed_account(UID1)
A.get_or_create_app_profile(UID1)
_src = inspect.getsource(A.get_user)
_get_user_keys = set(re.findall(r'"([a-z_0-9]+)"\s*:\s*row\[', _src))
_d = A._app_profile_to_user_dict(A.get_app_profile(UID1), account={"email": "s@example.com"})
check(set(_d.keys()) == _get_user_keys and len(_get_user_keys) > 60,
      f"11 dict adapté = mêmes clés que get_user() ({len(_get_user_keys)} clés)")

# 12. get_system_prompt sans KeyError
_prompt = A.get_system_prompt(_d, _d["guide"])
check(isinstance(_prompt, str) and len(_prompt) > 200,
      "12 get_system_prompt(dict adapté) -> str, aucun KeyError")

# 13. persona selena
_nom_selena = A.GUIDES["selena"].get("nom", "selena")
check(_nom_selena in _prompt, f"13 persona selena présente ('{_nom_selena}')")

# 14. persona différente après changement de guide
A.update_app_profile(UID1, guide="maia")
_d2 = A._app_profile_to_user_dict(A.get_app_profile(UID1))
_prompt_maia = A.get_system_prompt(_d2, _d2["guide"])
_nom_maia = A.GUIDES["maia"].get("nom", "maia")
check(_nom_maia in _prompt_maia and _prompt_maia != _prompt,
      f"14 persona '{_nom_maia}' après changement de guide, prompt différent")

# 15/16. add_message_for_user_id -> user_id renseigné, phone NULL
reset_db()
seed_account(UID1)
A.get_or_create_app_profile(UID1)
A.add_message_for_user_id(UID1, "user", "bonjour")
check(FAKE["messages"][-1]["user_id"] == UID1, "15 add_message_for_user_id écrit user_id")
check(FAKE["messages"][-1]["phone"] is None, "16 phone reste NULL pour un message app")

# 17. historique user_id -> bons messages
A.add_message_for_user_id(UID1, "assistant", "je suis là")
hist = A.get_history_for_user_id(UID1)
check(hist == [{"role": "user", "content": "bonjour"},
               {"role": "assistant", "content": "je suis là"}],
      "17 get_history_for_user_id -> messages du bon user, ordre ancien->récent")

# 18. jamais mélangé avec un autre user_id
seed_account(UID2)
A.get_or_create_app_profile(UID2)
A.add_message_for_user_id(UID2, "user", "autre personne")
check(A.get_history_for_user_id(UID2) == [{"role": "user", "content": "autre personne"}]
      and len(A.get_history_for_user_id(UID1)) == 2,
      "18 historiques de deux user_id jamais mélangés")

# 19. ordre chronologique cohérent avec get_history legacy (DESC puis reversed)
for i in range(3, 8):
    A.add_message_for_user_id(UID1, "user", f"m{i}")
h = A.get_history_for_user_id(UID1, limit=3)
check([m["content"] for m in h] == ["m5", "m6", "m7"],
      "19 get_history_for_user_id : LIMIT sur les plus récents, rendu ancien->récent")

# 20. get_conversation_for_user_id
conv = A.get_conversation_for_user_id(UID2)
check(conv == [("user", "autre personne", conv[0][2])] and len(conv[0]) == 3,
      "20 get_conversation_for_user_id -> (role, content, timestamp) ASC")

# 21. helpers legacy inchangés (signatures + SQL clé + fonctionnel)
check(str(inspect.signature(A.get_user)) == "(phone)", "21a get_user(phone) inchangé")
check(str(inspect.signature(A.add_message)) == "(phone, role, content, meta_message_id='')",
      "21b add_message(phone, ...) inchangé")
check(str(inspect.signature(A.get_history)) == "(phone, limit=20)", "21c get_history(phone, ...) inchangé")
check("WHERE phone=%s" in inspect.getsource(A.get_history)
      and "WHERE phone=%s" in inspect.getsource(A.get_conversation),
      "21d get_history / get_conversation legacy filtrent toujours sur phone")
check("INSERT INTO messages (phone,role,content,timestamp,meta_message_id)" in inspect.getsource(A.add_message),
      "21e add_message legacy écrit toujours phone")
reset_db()
A.add_message("+33612345678", "user", "salut legacy")
check(FAKE["messages"][-1]["phone"] == "+33612345678" and FAKE["messages"][-1]["user_id"] is None,
      "21f add_message legacy : phone renseigné, user_id NULL")
check(A.get_history("+33612345678") == [{"role": "user", "content": "salut legacy"}],
      "21g get_history legacy retourne le message")

# 22. signature get_reply inchangée
check(str(inspect.signature(A.get_reply)) ==
      "(phone, user_message, depuis_pub=False, user_msg_pre_inserted=False)",
      "22 get_reply : signature inchangée")

# 23. routes auth B2 toujours présentes
_rules = {r.rule for r in A.app.url_map.iter_rules()}
check({"/api/auth/request-code", "/api/auth/verify-code",
       "/api/auth/logout", "/api/account"}.issubset(_rules),
      "23 routes auth B2 (/api/auth/*, /api/account) toujours enregistrées")

# ---------------------------------------------------------------------------
print()
_total = _STATE["pass"] + _STATE["fail"]
if _STATE["fail"] == 0:
    print(f"✅ {_STATE['pass']}/{_total} tests passés")
    sys.exit(0)
else:
    print(f"❌ {_STATE['fail']} test(s) en échec sur {_total}")
    sys.exit(1)
