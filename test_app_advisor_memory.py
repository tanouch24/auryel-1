"""
test_app_advisor_memory.py — B7 : mémoire inter-session PAR CONSEILLER.

100 % local : psycopg2 mocké, get_conn -> fausse DB en mémoire (accounts /
app_profiles / messages / consultations / user_advisor_memory), call_llm mocké
(résumé ET réponse), aucun réseau, aucune vraie API. Style aligné sur
test_app_chat.py / test_app_consultation.py.

Couvre (cf. cahier B7 §14) :
  A mémoire absente -> aucun crash
  B première reprise Théa -> mémoire Théa créée
  C reprise suivante Théa -> ancien + nouveau fusionnés (curseur avancé)
  D last_source_message_id avance
  E aucun message déjà résumé repris (2e reprise sans nouveau msg -> 0 LLM)
  F Séléna ne reçoit pas la mémoire Théa
  G autre user isolé
  H UNIQUE (user_id, advisor_id) : une seule ligne par couple
  I mémoire injectée dans le prompt Théa
  J mémoire absente du prompt Séléna
  K mémoire vide non injectée
  L historique complet jamais injecté comme mémoire (seul le summary l'est)
  M cap résumé respecté (~1500 car.)
  N interprétation assistant : le prompt mémoire exige la distinction fait/hypo
  O signal aigu / moment grave -> refresh skip + injection skip
  P consultation/timer : suites existantes (rappel)
  Q tirage : suites existantes (rappel)
  R persona : advisor_override toujours respecté
  S LLM résumé mocké
  T LLM réponse mocké
  U aucun appel externe réel
  + scénario Théa -> Séléna -> Théa
  + scénario continuité "Julie"
"""

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.modules.setdefault("psycopg2", __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock())

import os
for _k, _v in {
    "SECRET_KEY": "test-secret-key", "VERIFY_TOKEN": "test", "ADMIN_PASSWORD": "test",
    "CRON_SECRET": "test", "DATABASE_URL": "postgresql://test:test@localhost/test",
    "STRIPE_SK": "sk_test_placeholder", "STRIPE_WEBHOOK_SECRET": "whsec_placeholder",
    "WHATSAPP_TOKEN": "test", "PHONE_NUMBER_ID": "test", "GROQ_API_KEY": "test",
    "RESEND_API_KEY": "test", "META_APP_SECRET": "test", "DAILY_SECRET": "test",
    "SEO_SECRET": "test", "TAROT_MEDIA_UPLOAD_DISABLED": "1",
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


# ---------------------------------------------------------------------------
# Fausse base de données en mémoire
# ---------------------------------------------------------------------------
FAKE = {"accounts": [], "app_profiles": [], "messages": [],
        "consultations": [], "user_advisor_memory": [], "seq": [0]}


def reset_db():
    for k in ("accounts", "app_profiles", "messages", "consultations",
              "user_advisor_memory"):
        FAKE[k].clear()
    FAKE["seq"][0] = 0


def seed_account(user_id, email="u@example.com", deleted_at=None):
    FAKE["accounts"].append({"user_id": user_id, "email": email,
                             "deleted_at": deleted_at,
                             "first_consultation_used_at": None,
                             "first_free_seconds_remaining": 3600,
                             "purchased_seconds_remaining": 0})


def _blank_profile(uid, guide="selena", **over):
    row = {f: "" for f in A._APP_PROFILE_FIELDS}
    row["user_id"] = uid
    row["guide"] = guide
    for c in ("nb_echanges", "nb_echanges_decouverte", "nb_echanges_dernier_tirage",
              "nb_echanges_dernier_psaume"):
        row[c] = 0
    row["tirage_propose_en_attente"] = False
    row["onboarding_done"] = True
    row.update(over)
    return row


def seed_profile(uid, guide="selena", **over):
    FAKE["app_profiles"].append(_blank_profile(uid, guide, **over))


def seed_consultation(cid, user_id, advisor_id):
    FAKE["consultations"].append({"id": cid, "user_id": user_id,
                                  "advisor_id": advisor_id})


def seed_message(user_id, role, content, consultation_id):
    FAKE["seq"][0] += 1
    FAKE["messages"].append({"id": FAKE["seq"][0], "user_id": user_id, "phone": None,
                             "role": role, "content": content,
                             "timestamp": datetime.now().isoformat(),
                             "consultation_id": consultation_id})
    return FAKE["seq"][0]


def mem_row(user_id, advisor_id):
    return next((m for m in FAKE["user_advisor_memory"]
                 if m["user_id"] == user_id and m["advisor_id"] == advisor_id), None)


_APP_SELECT = "SELECT " + ", ".join(A._APP_PROFILE_FIELDS) + " FROM app_profiles WHERE user_id=%s"
_APP_INSERT = ("INSERT INTO app_profiles (user_id, guide, onboarding_done, date_premier_contact, "
               "date_dernier_contact) VALUES (%s, 'selena', FALSE, %s, %s) ON CONFLICT (user_id) DO NOTHING")


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
        p = tuple(params) if params is not None else ()
        self._result = None
        self._rows = None
        self.rowcount = -1

        # ---- accounts ----
        if k == "SELECT deleted_at FROM accounts WHERE user_id=%s":
            r = next((a for a in FAKE["accounts"] if a["user_id"] == p[0]), None)
            self._result = (r["deleted_at"],) if r else None
        elif k == "SELECT email FROM accounts WHERE user_id=%s":
            r = next((a for a in FAKE["accounts"] if a["user_id"] == p[0]), None)
            self._result = (r["email"],) if r else None

        # ---- app_profiles ----
        elif k == _APP_SELECT:
            r = next((x for x in FAKE["app_profiles"] if x["user_id"] == p[0]), None)
            self._result = tuple(r[f] for f in A._APP_PROFILE_FIELDS) if r else None
        elif k == _APP_INSERT:
            uid, dpc, ddc = p
            if not any(x["user_id"] == uid for x in FAKE["app_profiles"]):
                row = _blank_profile(uid)
                row["onboarding_done"] = False
                row["date_premier_contact"] = dpc
                row["date_dernier_contact"] = ddc
                FAKE["app_profiles"].append(row)
        elif k.startswith("UPDATE app_profiles SET ") and k.endswith(" WHERE user_id=%s"):
            set_part = k[len("UPDATE app_profiles SET "):-len(" WHERE user_id=%s")]
            cols = [seg.split("=")[0].strip() for seg in set_part.split(",")]
            uid = p[-1]
            vals = list(p[:-1])
            for r in FAKE["app_profiles"]:
                if r["user_id"] == uid:
                    for col, val in zip(cols, vals):
                        r[col] = val

        # ---- messages ----
        elif k == ("INSERT INTO messages (user_id, phone, role, content, timestamp, consultation_id) "
                   "VALUES (%s, NULL, %s, %s, %s, %s)"):
            uid, role, content, ts, cid = p
            FAKE["seq"][0] += 1
            FAKE["messages"].append({"id": FAKE["seq"][0], "user_id": uid, "phone": None,
                                     "role": role, "content": content, "timestamp": ts,
                                     "consultation_id": cid})
        elif k == "SELECT role,content FROM messages WHERE user_id=%s ORDER BY id DESC LIMIT %s":
            uid, limit = p
            rows = sorted([m for m in FAKE["messages"] if m["user_id"] == uid],
                          key=lambda m: m["id"], reverse=True)
            self._rows = [(m["role"], m["content"]) for m in rows[:limit]]

        # ---- user_advisor_memory (B7) ----
        elif k.startswith("SELECT summary FROM user_advisor_memory"):
            uid, adv = p
            r = mem_row(uid, adv)
            self._result = (r["summary"],) if r else None
        elif k.startswith("SELECT id, summary, last_source_message_id FROM user_advisor_memory"):
            uid, adv = p
            r = mem_row(uid, adv)
            self._result = (r["id"], r["summary"], r["last_source_message_id"]) if r else None
        elif k.startswith("SELECT m.id, m.role, m.content FROM messages m JOIN consultations c"):
            uid, adv, after_id, limit = p
            adv_cids = {c["id"] for c in FAKE["consultations"]
                        if c["user_id"] == uid and c["advisor_id"] == adv}
            rows = sorted(
                [m for m in FAKE["messages"]
                 if m["user_id"] == uid and m["consultation_id"] in adv_cids
                 and m["id"] > int(after_id) and m["role"] in ("user", "assistant")],
                key=lambda m: m["id"])
            self._rows = [(m["id"], m["role"], m["content"]) for m in rows[:int(limit)]]
        elif k.startswith("INSERT INTO user_advisor_memory"):
            mid, uid, adv, summ, lsmi, ca, ua = p
            existing = mem_row(uid, adv)
            if existing:                       # ON CONFLICT (user_id, advisor_id) DO UPDATE
                existing["summary"] = summ
                existing["last_source_message_id"] = lsmi
                existing["updated_at"] = ua
            else:
                FAKE["user_advisor_memory"].append(
                    {"id": mid, "user_id": uid, "advisor_id": adv, "summary": summ,
                     "last_source_message_id": lsmi, "created_at": ca, "updated_at": ua})
        elif k.startswith("UPDATE user_advisor_memory SET summary=%s"):
            summ, lsmi, ua, mid = p
            for r in FAKE["user_advisor_memory"]:
                if r["id"] == mid:
                    r["summary"] = summ
                    r["last_source_message_id"] = lsmi
                    r["updated_at"] = ua
        else:
            raise AssertionError(f"SQL non géré par le fake B7 : {k}")


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

U_THEA = "11111111-1111-4111-8111-111111111111"
U_OTHER = "22222222-2222-4222-8222-222222222222"
NOW = datetime.now(timezone.utc)


def _capture_reply(user_id, msg, advisor):
    """Appelle get_reply_for_user_id et renvoie (reply, system_prompt_capturé)."""
    box = {}

    def fake_llm(messages, **kw):
        box["system"] = messages[0]["content"]
        return "Réponse conseiller."

    with patch.object(A, "call_llm", side_effect=fake_llm):
        reply = A.get_reply_for_user_id(user_id, msg, advisor_override=advisor,
                                        consultation_id="cid-x")
    return reply, box.get("system", "")


print("-" * 64)
print("A / S / T / U — mémoire absente, aucun crash, LLM mocké")
reset_db()
seed_account(U_THEA)
seed_profile(U_THEA, guide="thea")
_reply, _sys = _capture_reply(U_THEA, "Bonjour", "thea")
check(_reply == "Réponse conseiller.", "A get_reply_for_user_id OK sans aucune mémoire")
check("=== CONTINUITÉ UTILE ===" not in _sys, "K bloc mémoire absent quand aucune ligne")

print("-" * 64)
print("B / D / I / R — première reprise Théa : création mémoire + injection")
reset_db()
seed_account(U_THEA)
seed_profile(U_THEA, guide="thea")
seed_consultation("c-thea-1", U_THEA, "thea")
seed_message(U_THEA, "user", "Je suis séparé de Julie depuis trois mois.", "c-thea-1")
last_id = seed_message(U_THEA, "assistant", "Je t'entends. Cette séparation te pèse encore.", "c-thea-1")

with patch.object(A, "call_llm", return_value="La personne dit qu'elle est séparée de Julie depuis trois mois. Elle hésite à reprendre contact. Sujet récurrent : le manque et la culpabilité.") as m_sum:
    A.maybe_refresh_advisor_memory(U_THEA, "thea", now=NOW)

row = mem_row(U_THEA, "thea")
check(row is not None, "B mémoire Théa créée à la première reprise")
check(m_sum.call_count == 1, "S appel LLM de résumé effectué (mocké)")
check(row and "Julie" in row["summary"], "B le résumé contient l'info utile (Julie)")
check(row and row["last_source_message_id"] == last_id, "D last_source_message_id = dernier msg résumé")

_reply, _sys = _capture_reply(U_THEA, "Je repense à elle ce soir.", "thea")
check("=== CONTINUITÉ UTILE ===" in _sys, "I mémoire injectée dans le prompt Théa")
check("Julie" in _sys, "I le contenu mémoire (Julie) est bien dans le system prompt")
check("selon ma mémoire" not in _sys.lower() or "ne dis jamais" in _sys.lower(),
      "I consigne anti-robotique présente")
check("Théa" in _sys or "thea" in _sys.lower(), "R persona Théa toujours injecté (advisor_override)")

print("-" * 64)
print("L — seul le summary est injecté, jamais l'historique brut complet")
raw_user_line = "Je suis séparé de Julie depuis trois mois."
check(raw_user_line not in _sys.split("=== CONTINUITÉ UTILE ===")[1].split("[")[0]
      if "=== CONTINUITÉ UTILE ===" in _sys else False,
      "L le verbatim du 1er message n'est pas recopié dans le bloc mémoire")

print("-" * 64)
print("C / D / E — reprise suivante : fusion + curseur + rien de déjà résumé")
prev_cursor = mem_row(U_THEA, "thea")["last_source_message_id"]
new_id = seed_message(U_THEA, "user", "Je lui ai écrit hier, sans réponse.", "c-thea-1")
seed_message(U_THEA, "assistant", "Tu as fait un pas. Reste à voir ce que ça t'apprend.", "c-thea-1")
with patch.object(A, "call_llm", return_value="Séparée de Julie depuis 3 mois. NOUVEAU : a écrit à Julie, sans réponse pour l'instant. Question ouverte : faut-il insister.") as m_sum2:
    A.maybe_refresh_advisor_memory(U_THEA, "thea", now=NOW)
row = mem_row(U_THEA, "thea")
check(m_sum2.call_count == 1, "C 2e résumé : 1 appel LLM")
check(row["last_source_message_id"] > prev_cursor, "D curseur avancé après 2e reprise")
check("écrit" in row["summary"], "C nouveau fait fusionné dans le résumé")

# 3e reprise SANS nouveau message -> aucun appel LLM
with patch.object(A, "call_llm", return_value="NE DOIT PAS ÊTRE APPELÉ") as m_sum3:
    A.maybe_refresh_advisor_memory(U_THEA, "thea", now=NOW)
check(m_sum3.call_count == 0, "E aucun nouveau message -> 0 appel LLM, rien de re-résumé")

print("-" * 64)
print("H — UNIQUE (user_id, advisor_id) : toujours une seule ligne Théa")
rows_thea = [m for m in FAKE["user_advisor_memory"]
             if m["user_id"] == U_THEA and m["advisor_id"] == "thea"]
check(len(rows_thea) == 1, "H une seule ligne mémoire pour (U_THEA, thea)")

print("-" * 64)
print("F / J — Séléna ne reçoit pas la mémoire Théa")
seed_consultation("c-sel-1", U_THEA, "selena")
seed_message(U_THEA, "user", "Bonjour Séléna, autre sujet : mon travail.", "c-sel-1")
seed_message(U_THEA, "assistant", "Raconte-moi ce qui se joue au travail.", "c-sel-1")
with patch.object(A, "call_llm", return_value="La personne veut parler de son travail avec Séléna.") as m_sel:
    A.maybe_refresh_advisor_memory(U_THEA, "selena", now=NOW)
row_sel = mem_row(U_THEA, "selena")
check(row_sel is not None and "Julie" not in row_sel["summary"],
      "F la mémoire Séléna ne contient rien de la relation Julie (isolation par advisor_id)")
_reply, _sys_sel = _capture_reply(U_THEA, "Mon chef m'ignore.", "selena")
check("Julie" not in _sys_sel, "J le prompt Séléna ne contient jamais la mémoire Théa")
check("travail" in _sys_sel, "J le prompt Séléna contient bien SA propre mémoire")

print("-" * 64)
print("Théa -> Séléna -> Théa : retour Théa, mémoire Théa retrouvée intacte")
_reply, _sys_back = _capture_reply(U_THEA, "Je reviens vers toi Théa.", "thea")
check("=== CONTINUITÉ UTILE ===" in _sys_back and "Julie" in _sys_back,
      "retour Théa : mémoire Théa réinjectée")
check("travail" not in _sys_back.split("=== CONTINUITÉ UTILE ===")[1],
      "retour Théa : la mémoire Séléna ne fuite pas dans le prompt Théa")

print("-" * 64)
print("G — autre user totalement isolé")
reset_db()
seed_account(U_THEA); seed_profile(U_THEA, guide="thea")
seed_account(U_OTHER); seed_profile(U_OTHER, guide="thea")
seed_consultation("c-t", U_THEA, "thea")
seed_consultation("c-o", U_OTHER, "thea")
seed_message(U_THEA, "user", "Moi c'est la question de Julie.", "c-t")
seed_message(U_OTHER, "user", "Moi c'est un déménagement à Lyon.", "c-o")
with patch.object(A, "call_llm", return_value="Parle de Julie."):
    A.maybe_refresh_advisor_memory(U_THEA, "thea", now=NOW)
with patch.object(A, "call_llm", return_value="Parle d'un déménagement à Lyon."):
    A.maybe_refresh_advisor_memory(U_OTHER, "thea", now=NOW)
check("Julie" in mem_row(U_THEA, "thea")["summary"]
      and "Lyon" not in mem_row(U_THEA, "thea")["summary"], "G user A : mémoire propre")
check("Lyon" in mem_row(U_OTHER, "thea")["summary"]
      and "Julie" not in mem_row(U_OTHER, "thea")["summary"], "G user B isolé de user A")

print("-" * 64)
print("M — cap ~1500 caractères appliqué avant écriture")
reset_db()
seed_account(U_THEA); seed_profile(U_THEA, guide="thea")
seed_consultation("c1", U_THEA, "thea")
seed_message(U_THEA, "user", "blabla", "c1")
with patch.object(A, "call_llm", return_value="X" * 5000):
    A.maybe_refresh_advisor_memory(U_THEA, "thea", now=NOW)
check(len(mem_row(U_THEA, "thea")["summary"]) <= A._MEMORY_SUMMARY_CHAR_CAP,
      f"M résumé tronqué à {A._MEMORY_SUMMARY_CHAR_CAP} car. max")
check(A._MEMORY_SUMMARY_CHAR_CAP == 1500, "M cap technique = 1500 caractères")

print("-" * 64)
print("N — le prompt mémoire exige la distinction fait exprimé / hypothèse conseiller")
sp = A._MEMORY_UPDATE_SYSTEM_PROMPT.lower()
check("hypothèse" in sp and "interprétation" in sp and "prédiction" in sp,
      "N interdiction de transformer une hypothèse/prédiction du conseiller en fait")
check("n'ajoute aucun fait absent" in sp, "N interdiction d'ajouter un fait absent des messages")
check("prompt conseiller" not in sp and "voyante" not in sp,
      "O/N prompt mémoire SÉPARÉ du prompt persona conseiller")

print("-" * 64)
print("O — signal aigu / moment grave : refresh skip + injection skip")
reset_db()
seed_account(U_THEA)
# profil avec signal aigu très récent
seed_profile(U_THEA, guide="thea",
             dernier_signal_aigu_at=datetime.now().isoformat())
# mémoire existante non vide
FAKE["user_advisor_memory"].append(
    {"id": "m-0", "user_id": U_THEA, "advisor_id": "thea",
     "summary": "La personne dit être séparée de Julie.",
     "last_source_message_id": 0, "created_at": NOW, "updated_at": NOW})
_reply, _sys_crise = _capture_reply(U_THEA, "je vais très mal", "thea")
check("=== CONTINUITÉ UTILE ===" not in _sys_crise,
      "O injection mémoire sautée quand signal aigu récent au profil")

prof = _blank_profile(U_THEA, guide="thea")
prof["dernier_signal_aigu_at"] = datetime.now().isoformat()
check(A._signal_aigu_recent(A._app_profile_to_user_dict(prof), fenetre_heures=24) is True,
      "O garde endpoint : _signal_aigu_recent -> True (le refresh serait sauté)")
check(A.detecter_moment_grave("j'ai perdu ma mère la semaine dernière") is True,
      "O garde endpoint : detecter_moment_grave -> True (le refresh serait sauté)")

print("-" * 64)
print("Scénario continuité 'Julie' (B7 §16)")
reset_db()
seed_account(U_THEA)
seed_profile(U_THEA, guide="thea")
seed_consultation("cons-1", U_THEA, "thea")
seed_message(U_THEA, "user",
             "Je suis séparé de Julie depuis trois mois et j'hésite à lui écrire.", "cons-1")
seed_message(U_THEA, "assistant",
             "Cette hésitation dit quelque chose. Qu'est-ce qui te retient ?", "cons-1")
# reprise ultérieure -> refresh AVANT réponse
with patch.object(A, "call_llm", return_value="La personne dit être séparée de Julie depuis trois mois. Elle hésite à lui écrire ; c'est la question ouverte du moment."):
    A.maybe_refresh_advisor_memory(U_THEA, "thea", now=NOW)
_reply, _sys_cont = _capture_reply(U_THEA, "Alors, je fais quoi ?", "thea")
seg = _sys_cont.split("=== CONTINUITÉ UTILE ===")[1] if "=== CONTINUITÉ UTILE ===" in _sys_cont else ""
check("Julie" in seg and "séparé" in seg,
      "§16 le prompt Théa de la reprise contient l'info Julie de façon compacte")
check(len(seg) < 1800, "§16 la continuité injectée reste compacte (pas tout l'historique)")

# Séléna en parallèle : pas de mémoire Julie
seed_profile.__wrapped__ if hasattr(seed_profile, "__wrapped__") else None
FAKE["app_profiles"].clear()
seed_profile(U_THEA, guide="selena")
_reply, _sys_sel2 = _capture_reply(U_THEA, "Bonjour Séléna.", "selena")
check("Julie" not in _sys_sel2, "§16 Séléna ne reçoit pas la mémoire spécifique Théa")

print("-" * 64)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
