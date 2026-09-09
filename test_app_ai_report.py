"""
test_app_ai_report.py — « Signaler cette réponse » : POST /api/app/ai/report.

100 % local : psycopg2 mocké, get_conn -> fausse DB en mémoire (tables
`messages`, `consultations`, `ai_reports`). resolve_app_session monkeypatché.
Aucun LLM, aucun réseau. Style aligné sur test_app_support.py.

Couvre la matrice exigée (Partie A) :
  sans Bearer / autre utilisateur / consultation inconnue / message inconnu /
  message utilisateur au lieu d'assistant / motif invalide / commentaire trop
  long / succès / double envoi idempotent / aucune donnée de consultation
  renvoyée.
"""

import sys
import inspect
from datetime import datetime, timezone
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
CID1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"   # consultation de UID1
CID2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"   # consultation de UID2
CID_UNKNOWN = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

# messages : id -> {user_id, role, consultation_id}
_MESSAGES = {}
_CONSULTATIONS = {}     # id -> user_id
_AI_REPORTS = []        # lignes insérées


def _reset():
    _MESSAGES.clear()
    _CONSULTATIONS.clear()
    _AI_REPORTS.clear()
    _CONSULTATIONS[CID1] = UID1
    _CONSULTATIONS[CID2] = UID2
    # 10 = réponse assistant de UID1 dans CID1 ; 11 = message user de UID1 ;
    # 20 = réponse assistant de UID2 (autre compte).
    _MESSAGES[10] = {"user_id": UID1, "role": "assistant", "consultation_id": CID1}
    _MESSAGES[11] = {"user_id": UID1, "role": "user", "consultation_id": CID1}
    _MESSAGES[20] = {"user_id": UID2, "role": "assistant", "consultation_id": CID2}


class _Cur:
    def __init__(self):
        self._one = None

    def fetchone(self):
        return self._one

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = " ".join(sql.split())
        p = params
        self._one = None
        if k == "SELECT role, consultation_id FROM messages WHERE id=%s AND user_id=%s":
            mid, uid = p
            m = _MESSAGES.get(mid)
            self._one = (m["role"], m["consultation_id"]) if (m and m["user_id"] == uid) else None
        elif k == "SELECT 1 FROM consultations WHERE id=%s AND user_id=%s":
            cid, uid = p
            self._one = (1,) if _CONSULTATIONS.get(cid) == uid else None
        elif k == ("SELECT 1 FROM ai_reports "
                   "WHERE user_id=%s AND message_id=%s AND reason=%s"):
            uid, mid, reason = p
            self._one = (1,) if any(
                r["user_id"] == uid and r["message_id"] == mid and r["reason"] == reason
                for r in _AI_REPORTS) else None
        elif k == ("SELECT 1 FROM ai_reports "
                   "WHERE user_id=%s AND consultation_id=%s AND message_id IS NULL "
                   "AND reason=%s"):
            uid, cid, reason = p
            self._one = (1,) if any(
                r["user_id"] == uid and r["consultation_id"] == cid
                and r["message_id"] is None and r["reason"] == reason
                for r in _AI_REPORTS) else None
        elif k.startswith("INSERT INTO ai_reports"):
            rid, uid, cid, mid, reason, comment = p[:6]
            _AI_REPORTS.append({"id": rid, "user_id": uid, "consultation_id": cid,
                                "message_id": mid, "reason": reason,
                                "comment": comment, "status": "received"})
        else:
            raise AssertionError("SQL non géré par le fake ai_report : " + k)


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
    {"session_id": "s", "user_id": _CURRENT["uid"], "email": "session@auryel.co"}
    if tok else None
)


def _as(uid):
    _CURRENT["uid"] = uid


def _post(body, tok="x"):
    hdr = {"Authorization": f"Bearer {tok}"} if tok else {}
    return client.post("/api/app/ai/report", headers=hdr, json=body)


# ===========================================================================
# 0. Route + garde-fous serveur-autorité (source)
# ===========================================================================
_rules = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
check(("/api/app/ai/report", "POST") in _rules, "0a POST /api/app/ai/report enregistrée")
_src = inspect.getsource(A.api_app_ai_report)
check('g.app_account["user_id"]' in _src, "0b user_id vient du Bearer")
check('data.get("user_id")' not in _src, "0c aucun user_id lu depuis le body")
check('@limiter.limit("10 per hour")' in _src, "0d rate limit 10/h déclaré")
check("_AI_REPORT_REASONS" in _src, "0e motif validé contre une allowlist")

# ===========================================================================
# 1. Auth obligatoire
# ===========================================================================
_reset()
check(_post({"reason": "unsafe", "message_id": "10"}, tok=None).status_code == 401,
      "1 sans Bearer -> 401")

# ===========================================================================
# 2. Motif invalide -> 400
# ===========================================================================
_reset(); _as(UID1)
check(_post({"reason": "je-deteste", "message_id": "10"}).status_code == 400,
      "2a motif hors allowlist -> 400")
check(_post({"message_id": "10"}).status_code == 400, "2b motif absent -> 400")
check(_post("pas un objet").status_code == 400, "2c body non-objet -> 400")

# ===========================================================================
# 3. Message inconnu / autre utilisateur -> 404
# ===========================================================================
_reset(); _as(UID1)
check(_post({"reason": "unsafe", "message_id": "9999"}).status_code == 404,
      "3a message inexistant -> 404")
check(_post({"reason": "unsafe", "message_id": "20"}).status_code == 404,
      "3b message d'un AUTRE compte -> 404 (jamais de fuite)")
check(_post({"reason": "unsafe", "message_id": "pas-un-entier"}).status_code == 404,
      "3c message_id non entier -> 404")

# ===========================================================================
# 4. Consultation inconnue / autre utilisateur -> 404
# ===========================================================================
_reset(); _as(UID1)
check(_post({"reason": "unsafe", "consultation_id": CID_UNKNOWN}).status_code == 404,
      "4a consultation inexistante -> 404")
check(_post({"reason": "unsafe", "consultation_id": CID2}).status_code == 404,
      "4b consultation d'un AUTRE compte -> 404")
check(_post({"reason": "unsafe", "consultation_id": "pas-un-uuid"}).status_code == 404,
      "4c consultation_id malformé -> 404")

# ===========================================================================
# 5. Message utilisateur (pas assistant) -> 400
# ===========================================================================
_reset(); _as(UID1)
r = _post({"reason": "unsafe", "message_id": "11"})
check(r.status_code == 400, "5a signaler un message UTILISATEUR -> 400")
check(len(_AI_REPORTS) == 0, "5b aucune ligne créée pour un message non signalable")

# ===========================================================================
# 6. Ni message_id ni consultation_id -> 400
# ===========================================================================
_reset(); _as(UID1)
check(_post({"reason": "unsafe"}).status_code == 400,
      "6 ni message_id ni consultation_id -> 400 invalid_request")

# ===========================================================================
# 7. Commentaire trop long -> 400
# ===========================================================================
_reset(); _as(UID1)
long_comment = "x" * (A._AI_REPORT_COMMENT_MAX + 1)
check(_post({"reason": "unsafe", "message_id": "10", "comment": long_comment}).status_code == 400,
      "7a commentaire au-delà de _AI_REPORT_COMMENT_MAX -> 400")
_reset(); _as(UID1)
check(_post({"reason": "unsafe", "message_id": "10",
            "comment": "x" * A._AI_REPORT_COMMENT_MAX}).status_code == 200,
      "7b commentaire pile à la limite -> 200")

# ===========================================================================
# 8. Succès : 200 {"status":"received"} + ligne persistée + commentaire échappé
# ===========================================================================
_reset(); _as(UID1)
r = _post({"reason": "unsafe", "message_id": "10",
           "comment": '<script>alert("x")</script>'})
check(r.status_code == 200 and r.get_json() == {"status": "received"},
      "8a succès -> 200 {status: received}")
check(len(_AI_REPORTS) == 1 and _AI_REPORTS[0]["message_id"] == 10
      and _AI_REPORTS[0]["consultation_id"] == CID1
      and _AI_REPORTS[0]["reason"] == "unsafe",
      "8b ligne ai_reports créée avec les bonnes références")
check("<script>" not in (_AI_REPORTS[0]["comment"] or "")
      and "&lt;script&gt;" in (_AI_REPORTS[0]["comment"] or ""),
      "8c commentaire échappé (anti-injection HTML)")

# ===========================================================================
# 9. Idempotence : double envoi identique -> 200, une seule ligne
# ===========================================================================
_reset(); _as(UID1)
r1 = _post({"reason": "unsafe", "message_id": "10"})
r2 = _post({"reason": "unsafe", "message_id": "10"})
check(r1.status_code == 200 and r2.status_code == 200
      and r1.get_json() == r2.get_json() == {"status": "received"},
      "9a deux envois identiques -> même réponse 200")
check(len(_AI_REPORTS) == 1, "9b une seule ligne ai_reports (pas de doublon)")
# un motif DIFFÉRENT sur le même message n'est pas un doublon
r3 = _post({"reason": "misleading", "message_id": "10"})
check(r3.status_code == 200 and len(_AI_REPORTS) == 2,
      "9c motif différent sur le même message -> nouvelle ligne")

# ===========================================================================
# 10. Idempotence côté consultation seule (message_id absent)
# ===========================================================================
_reset(); _as(UID1)
_post({"reason": "inappropriate", "consultation_id": CID1})
_post({"reason": "inappropriate", "consultation_id": CID1})
check(len(_AI_REPORTS) == 1 and _AI_REPORTS[0]["message_id"] is None,
      "10 double signalement au niveau consultation -> une seule ligne")

# ===========================================================================
# 11. Aucune donnée de consultation renvoyée
# ===========================================================================
_reset(); _as(UID1)
body_txt = _post({"reason": "unsafe", "message_id": "10"}).get_data(as_text=True)
check(body_txt.strip() == '{"status":"received"}'
      or _post({"reason": "misleading", "message_id": "10"}).get_json() == {"status": "received"},
      "11a réponse = uniquement {status: received}")
check("content" not in body_txt and "message" not in body_txt.lower().replace("received", ""),
      "11b aucun contenu / historique de consultation dans la réponse")

# ===========================================================================
# 12. user_id du body ignoré : un pirate ne signale pas pour un autre compte
# ===========================================================================
_reset(); _as(UID1)
r = _post({"reason": "unsafe", "message_id": "10", "user_id": UID2})
check(r.status_code == 200 and _AI_REPORTS[0]["user_id"] == UID1,
      "12 user_id du body ignoré -> ligne attribuée au compte du Bearer")

# ===========================================================================
# 13. consultation_id incohérent avec le message -> 404
# ===========================================================================
_reset(); _as(UID1)
check(_post({"reason": "unsafe", "message_id": "10", "consultation_id": CID2}).status_code == 404,
      "13 consultation_id ne correspond pas au fil du message -> 404")

# ---------------------------------------------------------------------------
print("-" * 60)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
