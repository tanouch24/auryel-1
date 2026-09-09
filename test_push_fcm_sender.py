"""
test_push_fcm_sender.py — Envoi FCM HTTP v1 (module push_fcm).

100 % local : aucun réseau, aucune vraie clé. Le transport HTTP et
l'obtention du jeton OAuth sont injectés (fakes). On vérifie :
  - forme du message HTTP v1 (allowlist type, visibilité privée, pas de
    contenu de consultation) ;
  - dry-run = validate_only, aucune livraison ;
  - PUSH_ENABLED=false -> aucun envoi ;
  - UNREGISTERED / INVALID_ARGUMENT -> permanent, jeton à invalider ;
  - 429 / 503 -> transitoire, retenté puis abandonné proprement ;
  - config absente -> échec propre, pas d'exception qui remonte ;
  - le jeton FCM n'apparaît jamais dans un message d'erreur.
Style aligné sur test_app_support.py (check() + compteur).
"""

import json

import push_fcm as P

_STATE = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _STATE["pass"] += 1
        print(f"OK  {label}")
    else:
        _STATE["fail"] += 1
        print(f"XX  {label}")


TOKEN = "fake-device-token-AAAABBBBCCCC"
SA_JSON = json.dumps({
    "type": "service_account", "project_id": "auryel-f9e40",
    "private_key_id": "x", "private_key": "x", "client_email": "x@x.iam",
    "client_id": "1", "token_uri": "https://oauth2.googleapis.com/token",
})


class _FakeResp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _FakeHttp:
    """Transport injecté : renvoie des réponses scriptées, capture les appels."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json,
                           "timeout": timeout})
        return self._responses.pop(0)


def _sender(responses, enabled=True, dry_run=False):
    cfg = P.FcmConfig(
        service_account_json=SA_JSON,
        project_id="auryel-f9e40",
        enabled=enabled,
        dry_run=dry_run,
    )
    return P.FcmSender(
        cfg,
        http=_FakeHttp(responses),
        access_token_provider=lambda: "ya29.fake-access-token",
    )


# --- 1. message bien formé -------------------------------------------------
s = _sender([_FakeResp(200, {"name": "projects/auryel-f9e40/messages/42"})])
res = s.send(TOKEN, "daily_thought", "Ta pensée du jour t'attend",
             "Ouvre Auryel pour la découvrir.")
call = s._http.calls[0]
msg = call["json"]["message"]
check(res.outcome == "sent", "1a — 200 -> outcome=sent")
check(res.provider_message_id == "projects/auryel-f9e40/messages/42",
      "1b — message id renvoyé")
check(call["url"].endswith("/projects/auryel-f9e40/messages:send"),
      "1c — endpoint HTTP v1 officiel")
check(call["headers"]["Authorization"] == "Bearer ya29.fake-access-token",
      "1d — Authorization Bearer <access token>")
check(msg["token"] == TOKEN, "1e — token cible dans message.token")
check(msg["data"]["type"] == "daily_thought", "1f — data.type = catégorie")
check(msg["android"]["notification"]["visibility"] == "PRIVATE",
      "1g — visibilité écran verrouillé = PRIVATE")
check("url" not in json.dumps(msg).lower() or "http" not in str(msg.get("data", {})),
      "1h — aucune URL arbitraire dans data")
check(call["timeout"] is not None and call["timeout"] <= 15,
      "1i — timeout strict (<= 15 s)")

# --- 2. allowlist des types ---------------------------------------------------
s = _sender([_FakeResp(200, {"name": "n"})])
try:
    s.send(TOKEN, "promo_spam", "x", "y")
    check(False, "2 — type hors allowlist -> ValueError")
except ValueError:
    check(True, "2 — type hors allowlist -> ValueError")

# --- 3. dry-run = validate_only, aucune livraison ---------------------------
s = _sender([_FakeResp(200, {})], dry_run=True)
res = s.send(TOKEN, "daily_meditation", "Ton moment du soir", "…")
check(res.outcome == "dry_run", "3a — dry_run -> outcome=dry_run")
check(s._http.calls[0]["json"].get("validate_only") is True,
      "3b — payload porte validate_only=true")

# --- 4. PUSH_ENABLED=false -> aucun envoi ----------------------------------
s = _sender([], enabled=False)
res = s.send(TOKEN, "daily_thought", "x", "y")
check(res.outcome == "skipped_disabled", "4a — disabled -> skipped_disabled")
check(s._http.calls == [], "4b — disabled -> aucun appel HTTP")

# --- 5. UNREGISTERED -> permanent, jeton à invalider ----------------------
s = _sender([_FakeResp(404, {"error": {"status": "NOT_FOUND", "details": [
    {"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError",
     "errorCode": "UNREGISTERED"}]}})])
res = s.send(TOKEN, "daily_thought", "x", "y")
check(res.outcome == "invalid_token", "5a — UNREGISTERED -> invalid_token")
check(res.permanent is True, "5b — invalid_token est permanent")

# --- 6. INVALID_ARGUMENT -> permanent ------------------------------------
s = _sender([_FakeResp(400, {"error": {"status": "INVALID_ARGUMENT", "details": [
    {"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError",
     "errorCode": "INVALID_ARGUMENT"}]}})])
res = s.send(TOKEN, "daily_thought", "x", "y")
check(res.outcome == "invalid_token", "6 — INVALID_ARGUMENT -> invalid_token")

# --- 7. 503 transitoire : retenté puis abandonné ------------------------
s = _sender([_FakeResp(503, {"error": {"status": "UNAVAILABLE"}}),
             _FakeResp(503, {"error": {"status": "UNAVAILABLE"}}),
             _FakeResp(503, {"error": {"status": "UNAVAILABLE"}})])
res = s.send(TOKEN, "daily_thought", "x", "y", max_attempts=3, backoff_base=0)
check(res.outcome == "transient_error", "7a — 503 x3 -> transient_error")
check(res.permanent is False, "7b — transitoire non permanent")
check(len(s._http.calls) == 3, "7c — 3 tentatives effectuées")

# --- 8. 503 puis 200 : succès après retry -------------------------------
s = _sender([_FakeResp(503, {"error": {"status": "UNAVAILABLE"}}),
             _FakeResp(200, {"name": "ok"})])
res = s.send(TOKEN, "daily_thought", "x", "y", max_attempts=3, backoff_base=0)
check(res.outcome == "sent", "8 — 503 puis 200 -> sent")

# --- 9. config absente -> échec propre, pas d'exception ----------------
cfg = P.FcmConfig(service_account_json="", project_id="", enabled=True,
                  dry_run=False)
s = P.FcmSender(cfg, http=_FakeHttp([]),
                access_token_provider=lambda: (_ for _ in ()).throw(
                    RuntimeError("no creds")))
res = s.send(TOKEN, "daily_thought", "x", "y")
check(res.outcome == "config_error", "9a — config absente -> config_error")
check(s._http.calls == [], "9b — config absente -> aucun appel HTTP")

# --- 10. le jeton FCM n'apparaît jamais dans l'erreur -----------------
s = _sender([_FakeResp(500, {"error": {"status": "INTERNAL",
             "message": f"boom {TOKEN}"}})])
res = s.send(TOKEN, "daily_thought", "x", "y", max_attempts=1, backoff_base=0)
check(TOKEN not in (res.error or ""), "10 — jeton absent du message d'erreur")

print(f"\n{_STATE['pass']} OK / {_STATE['fail']} XX")
raise SystemExit(1 if _STATE["fail"] else 0)
