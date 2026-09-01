"""
test_time_consultation_logic.py — TIMER-A.3c-1 : consultation LOGIQUE
persistante + règle conseiller (fenêtre d'activité), SANS câbler le POST.

Fake DB DÉDIÉ minimal (table `consultations` uniquement) : on NE réécrit PAS
les gros mocks legacy. Style script, aligné sur test_time_engine.py.

`get_or_open_time_consultation_tx` NE débite RIEN et n'est appelée par AUCUN
endpoint dans ce lot ; `api_consultation_message` / `open_or_get_consultation`
gardent le modèle 2 h.
"""

import sys
import ast
import inspect
import re
import textwrap
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

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

import auryel_bot as A

_STATE = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _STATE["pass"] += 1
        print(f"✅ {label}")
    else:
        _STATE["fail"] += 1
        print(f"❌ {label}")


def T(h, m, s=0):
    return datetime(2026, 9, 1, h, m, s, tzinfo=timezone.utc)


UID = "11111111-1111-1111-1111-111111111111"
GRACE = 300

# ---------------------------------------------------------------------------
# Fake DB dédié : SEULE la table consultations. Aucune table accounts /
# consultation_allowance / earned_credits / tirages / messages -> si la
# fonction tentait de les toucher, "SQL non géré" ferait échouer le test.
# ---------------------------------------------------------------------------
DB = {"consultations": [], "tirages": [], "messages": []}


def reset_db():
    DB["consultations"].clear()
    DB["tirages"].clear()
    DB["messages"].clear()


def seed_consultation(cid, advisor_id, started_at, last_activity_at=None,
                      billed_until=None, credit_source="monthly",
                      expires_at=None):
    DB["consultations"].append({
        "id": str(cid), "user_id": UID, "advisor_id": advisor_id,
        "started_at": started_at,
        "expires_at": expires_at or (started_at + timedelta(hours=2)),
        "credit_source": credit_source, "created_at": started_at,
        "last_activity_at": last_activity_at, "billed_until": billed_until,
    })


def _norm(sql):
    return " ".join(sql.split())


class FakeCursor:
    def __init__(self):
        self._result = None
        self.rowcount = -1

    def fetchone(self):
        return self._result

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = _norm(sql)
        p = tuple(params or ())
        self._result = None
        self.rowcount = -1

        if k == ("SELECT id, advisor_id, last_activity_at, billed_until "
                 "FROM consultations WHERE user_id=%s "
                 "ORDER BY started_at DESC LIMIT 1"):
            (uid,) = p
            rows = [c for c in DB["consultations"] if c["user_id"] == str(uid)]
            rows.sort(key=lambda c: c["started_at"], reverse=True)
            if rows:
                c0 = rows[0]
                self._result = (c0["id"], c0["advisor_id"],
                                c0["last_activity_at"], c0["billed_until"])

        elif k == ("INSERT INTO consultations (id, user_id, advisor_id, "
                   "started_at, expires_at, credit_source, created_at) "
                   "VALUES (%s, %s, %s, %s, %s, 'time', %s)"):
            cid, uid, advisor, started, expires, created = p
            DB["consultations"].append({
                "id": str(cid), "user_id": str(uid), "advisor_id": advisor,
                "started_at": started, "expires_at": expires,
                "credit_source": "time", "created_at": created,
                "last_activity_at": None, "billed_until": None,
            })
            self.rowcount = 1

        else:
            raise AssertionError("SQL non géré par le fake A.3c-1 : " + k)


def cur():
    return FakeCursor()


def call(preferred, now, time_left):
    return A.get_or_open_time_consultation_tx(cur(), UID, preferred, now, time_left)


def _code_only(fn):
    """Source de `fn` SANS docstring ni commentaires (ast.unparse) : on teste le
    CODE exécutable, pas la prose du docstring."""
    _t = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    _f = _t.body[0]
    if (_f.body and isinstance(_f.body[0], ast.Expr)
            and isinstance(getattr(_f.body[0], "value", None), ast.Constant)
            and isinstance(_f.body[0].value.value, str)):
        _f.body = _f.body[1:]
    return ast.unparse(_f)


# ---------------------------------------------------------------------------
print("-" * 64)
print("A — aucun historique -> nouvelle consultation")
reset_db()
r = call("selena", T(10, 0), 28800)
check(r["created"] is True and r["phase"] == "opened_first"
      and r["advisor_id"] == "selena"
      and len(DB["consultations"]) == 1
      and DB["consultations"][0]["advisor_id"] == "selena",
      "A nouvelle consultation créée avec preferred_advisor_id=selena")
check(DB["consultations"][0]["credit_source"] == "time",
      "A2 credit_source de compat = 'time'")
check(DB["consultations"][0]["expires_at"] == T(10, 0) + A._CONSULTATION_DUREE,
      "A3 expires_at de compat = now + _CONSULTATION_DUREE (jamais lu comme cutoff)")

print("-" * 64)
print("B — fenêtre active -> même consultation reprise")
reset_db()
seed_consultation("c-1", "selena", T(9, 0), last_activity_at=T(9, 58))
r = call("selena", T(10, 0), 28800)   # 10:00 < 9:58 + 5min = 10:03
check(r["created"] is False and r["phase"] == "resumed_active"
      and r["consultation_id"] == "c-1" and r["advisor_id"] == "selena"
      and len(DB["consultations"]) == 1,
      "B fenêtre active -> reprise de c-1, aucune création")

print("-" * 64)
print("C — fenêtre active + preferred_advisor DIFFÉRENT -> même consult, ancien advisor conservé")
reset_db()
seed_consultation("c-1", "selena", T(9, 0), last_activity_at=T(9, 58))
r = call("ezra", T(10, 0), 28800)
check(r["created"] is False and r["phase"] == "resumed_active"
      and r["consultation_id"] == "c-1" and r["advisor_id"] == "selena"
      and DB["consultations"][0]["advisor_id"] == "selena"
      and len(DB["consultations"]) == 1,
      "C fenêtre active + preferred=ezra -> reste c-1 / advisor=selena (jamais remplacé)")

print("-" * 64)
print("C-bis — fenêtre 'active' mais time_available == 0 -> PAS resumed_active")
reset_db()
seed_consultation("c-1", "selena", T(9, 0), last_activity_at=T(9, 58))
r = call("selena", T(10, 0), 0)
check(r["phase"] == "resumed_same_advisor" and r["created"] is False,
      "C-bis time_available 0 -> la fenêtre n'est pas 'active' ; même advisor -> reprise")

print("-" * 64)
print("D — fenêtre expirée + même advisor -> même consultation reprise")
reset_db()
seed_consultation("c-1", "selena", T(9, 0), last_activity_at=T(9, 50))  # +5min = 9:55 < 10:00
r = call("selena", T(10, 0), 28800)
check(r["created"] is False and r["phase"] == "resumed_same_advisor"
      and r["consultation_id"] == "c-1" and len(DB["consultations"]) == 1,
      "D fenêtre expirée + advisor identique -> reprise de c-1")

print("-" * 64)
print("E/F/G — fenêtre expirée + advisor DIFFÉRENT -> NOUVELLE consultation")
reset_db()
seed_consultation("c-old", "selena", T(9, 0), last_activity_at=T(9, 50))
# quelques messages/tirages rattachés à l'ancienne
DB["messages"].append({"id": 1, "consultation_id": "c-old", "role": "user"})
DB["tirages"].append({"id": "t-1", "consultation_id": "c-old"})
_msgs_before = [dict(m) for m in DB["messages"]]
_tir_before = [dict(t) for t in DB["tirages"]]
r = call("ezra", T(10, 0), 28800)
check(r["created"] is True and r["phase"] == "opened_new_advisor"
      and r["advisor_id"] == "ezra",
      "E fenêtre expirée + advisor différent -> NOUVELLE consultation (advisor=ezra)")
_old = next(c for c in DB["consultations"] if c["id"] == "c-old")
_new = next(c for c in DB["consultations"] if c["id"] == r["consultation_id"])
check(_old["advisor_id"] == "selena" and len(DB["consultations"]) == 2,
      "F ancienne consultation c-old INTACTE (advisor=selena), toujours en DB")
check(_new["id"] != "c-old" and _new["advisor_id"] == "ezra",
      "G nouveau consultation_id != ancien, advisor=ezra")
check(DB["messages"] == _msgs_before and DB["tirages"] == _tir_before,
      "S messages / tirages existants NON modifiés (toujours rattachés à c-old)")

print("-" * 64)
print("Q/R — pas de création si reprise cohérente ; deux appels séquentiels")
reset_db()
seed_consultation("c-1", "selena", T(9, 0), last_activity_at=T(9, 58))
call("selena", T(10, 0), 28800)
call("selena", T(10, 1), 28800)
check(len(DB["consultations"]) == 1,
      "R deux appels (fenêtre active, même advisor) -> AUCUNE nouvelle consultation")
reset_db()
seed_consultation("c-1", "selena", T(9, 0), last_activity_at=T(9, 50))
call("selena", T(10, 0), 28800)   # reprise (même advisor)
call("selena", T(10, 5), 28800)
check(len(DB["consultations"]) == 1,
      "Q reprise même advisor répétée -> toujours 1 seule consultation")

print("-" * 64)
print("H — la sélection n'utilise JAMAIS expires_at comme cutoff")
_code = _code_only(A.get_or_open_time_consultation_tx)
_sel = re.search(r"SELECT id, advisor_id, last_activity_at, billed_until.*?LIMIT 1",
                 _code, re.S)
check(_sel is not None and "expires_at" not in _sel.group(0),
      "H requête 'consultation la plus récente' : PAS de WHERE expires_at dans le SELECT")
check("expires_at" not in _code,
      "H2 le CODE de get_or_open_time_consultation_tx ne référence JAMAIS expires_at")

print("-" * 64)
print("I→N — aucun débit dans la nouvelle fonction (CODE only + preuve fake)")
_full_src = (_code_only(A.get_or_open_time_consultation_tx)
             + "\n" + _code_only(A._open_time_consultation_tx))
for _pat, _lbl in [
    (r"monthly_used\s*=\s*monthly_used\s*\+\s*1", "I monthly_used += 1"),
    (r"first_consultation_used_at", "N first_consultation_used_at"),
    (r"earned_credits", "J earned_credits"),
    (r"first_free_seconds_remaining", "K first_free_seconds_remaining"),
    (r"monthly_used_seconds", "L monthly_used_seconds"),
    (r"purchased_seconds_remaining", "M purchased_seconds_remaining"),
]:
    check(re.search(_pat, _full_src) is None,
          f"{_lbl} : jamais référencé par get_or_open_time_consultation_tx / _open_time_consultation_tx")
# preuve fonctionnelle : le fake n'a NI accounts NI allowance NI earned_credits.
# Les scénarios A→G ci-dessus se sont exécutés sans "SQL non géré" -> la
# fonction n'a émis QUE le SELECT consultations + l'INSERT consultations.
check(True, "I→N2 aucun scénario n'a déclenché de SQL sur accounts/allowance/earned/tirages/messages")

print("-" * 64)
print("O — aucun FOR UPDATE sur consultations")
check("FOR UPDATE" not in _full_src,
      "O CODE de get_or_open_time_consultation_tx / _open_time_consultation_tx : aucun FOR UPDATE")
check(re.search(r"FROM\s+consultations\b[^\"]*?FOR UPDATE", inspect.getsource(A))
      is None,
      "O2 aucun 'FROM consultations ... FOR UPDATE' nulle part dans le fichier")

print("-" * 64)
print("P — la fonction exige/documente le mutex accounts de l'appelant")
_doc = inspect.getdoc(A.get_or_open_time_consultation_tx) or ""
check("accounts" in _doc and "FOR UPDATE" in _doc
      and ("détient déjà" in _doc.lower() or "deja" in _doc.lower()
           or "déjà" in _doc.lower()),
      "P docstring : 'l'appelant détient déjà accounts FOR UPDATE'")

print("-" * 64)
print("ISOLATION — POST basculé (A.3c-2c) : passe par le helper de flux, pas les primitives")
# open_or_get_consultation : n'a jamais rien à voir avec la logique temps.
_oog = _code_only(A.open_or_get_consultation)
check("get_or_open_time_consultation_tx(" not in _oog
      and "_open_time_consultation_tx(" not in _oog
      and "_open_time_consultation_flow_tx(" not in _oog,
      "iso open_or_get_consultation : aucune logique consultation temps")
# api_consultation_message : appelle le HELPER DE FLUX, JAMAIS get_or_open_* /
# _open_time_consultation_tx directement.
_pm = _code_only(A.api_consultation_message)
check("_open_time_consultation_flow_tx(" in _pm,
      "iso api_consultation_message : bascule via _open_time_consultation_flow_tx")
check("get_or_open_time_consultation_tx(" not in _pm
      and "_open_time_consultation_tx(" not in _pm,
      "iso api_consultation_message : ne touche PAS get_or_open_time_consultation_tx / "
      "_open_time_consultation_tx en direct")
# get_or_open_time_consultation_tx : appelée UNIQUEMENT par le helper de flux.
import types as _types
_callers_gooc = []
_callers_flow = []
for _nm in dir(A):
    _o = getattr(A, _nm)
    if not isinstance(_o, _types.FunctionType):
        continue
    try:
        _cc = _code_only(_o)
    except (OSError, TypeError, SyntaxError):
        continue
    if _nm != "get_or_open_time_consultation_tx" and "get_or_open_time_consultation_tx(" in _cc:
        _callers_gooc.append(_nm)
    if _nm != "_open_time_consultation_flow_tx" and "_open_time_consultation_flow_tx(" in _cc:
        _callers_flow.append(_nm)
check(_callers_gooc == ["_open_time_consultation_flow_tx"],
      f"iso get_or_open_time_consultation_tx : appelée SEULEMENT par "
      f"_open_time_consultation_flow_tx (trouvé : {_callers_gooc})")
check(_callers_flow == ["api_consultation_message"],
      f"iso _open_time_consultation_flow_tx : appelé UNIQUEMENT par "
      f"api_consultation_message (trouvé : {_callers_flow})")

print("-" * 64)
print(f"RESULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
