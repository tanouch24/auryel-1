"""
push_fcm.py — Envoi de notifications via Firebase Cloud Messaging HTTP v1.

Module DÉDIÉ et TESTABLE. Aucune dépendance à Flask, à la session utilisateur
ou à la DB. Le transport HTTP et l'obtention du jeton d'accès OAuth2 sont
injectables (tests 100 % hors ligne).

Règles :
  - endpoint officiel : POST https://fcm.googleapis.com/v1/projects/<pid>/messages:send
  - auth serveur : service account -> access token OAuth2, scope
    https://www.googleapis.com/auth/firebase.messaging
  - timeout strict, retry UNIQUEMENT sur erreur transitoire (429, 5xx, réseau),
    jamais de retry sur erreur permanente
  - UNREGISTERED / INVALID_ARGUMENT -> jeton définitivement invalide
  - allowlist stricte des types (miroir NotificationType.wire côté Flutter)
  - visibilité écran verrouillé = PRIVATE ; titre/corps GÉNÉRIQUES fournis par
    l'appelant — AUCUN contenu de consultation, aucune donnée personnelle, aucune
    URL arbitraire n'est ajoutée ici
  - le jeton FCM n'est JAMAIS écrit dans un log ni dans un message d'erreur
  - PUSH_ENABLED=false : aucun envoi ; PUSH_DRY_RUN=true : validate_only ;
    configuration absente : échec propre (`config_error`), jamais d'exception
    qui casse le process appelant
"""

import json
import os
import time

FCM_SEND_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

# Allowlist stricte — doit rester synchronisée avec NotificationType.wire
# (Flutter) et _PUSH_CATEGORIES (auryel_bot.py).
ALLOWED_TYPES = (
    "daily_thought", "daily_meditation", "personal_guidance",
    "weekly_sleep", "weekly_life_lesson",
)

_PERMANENT_FCM_ERRORS = frozenset({
    "UNREGISTERED", "INVALID_ARGUMENT", "SENDER_ID_MISMATCH",
    "THIRD_PARTY_AUTH_ERROR",
})
_TRANSIENT_HTTP = frozenset({429, 500, 502, 503, 504})

_TITLE_MAX = 120
_BODY_MAX = 240
_ERR_MAX = 200


class FcmConfig:
    def __init__(self, service_account_json="", project_id="",
                 enabled=False, dry_run=True, timeout=10.0):
        self.service_account_json = service_account_json or ""
        self.project_id = project_id or ""
        self.enabled = bool(enabled)
        self.dry_run = bool(dry_run)
        self.timeout = float(timeout)

    @classmethod
    def from_env(cls, env=None):
        """Défauts SÛRS : disabled si variable absente, dry-run si ambiguïté."""
        env = env or os.environ
        raw_enabled = (env.get("PUSH_ENABLED", "") or "").strip().lower()
        raw_dry = (env.get("PUSH_DRY_RUN", "") or "").strip().lower()
        enabled = raw_enabled in ("1", "true", "yes", "on")
        # dry-run par défaut : seul un "false"/"0" explicite désactive le dry-run
        dry_run = raw_dry not in ("0", "false", "no", "off")
        return cls(
            service_account_json=env.get("FIREBASE_SERVICE_ACCOUNT_JSON", ""),
            project_id=env.get("FIREBASE_PROJECT_ID", ""),
            enabled=enabled,
            dry_run=dry_run,
        )

    @property
    def configured(self):
        return bool(self.service_account_json) and bool(self.project_id)


class FcmResult:
    __slots__ = ("outcome", "permanent", "provider_message_id", "error",
                 "attempts", "http_status")

    def __init__(self, outcome, permanent=False, provider_message_id=None,
                 error=None, attempts=0, http_status=None):
        self.outcome = outcome                       # sent | dry_run |
        #   skipped_disabled | config_error | invalid_token |
        #   transient_error | permanent_error
        self.permanent = permanent
        self.provider_message_id = provider_message_id
        self.error = error
        self.attempts = attempts
        self.http_status = http_status

    def __repr__(self):
        return (f"FcmResult(outcome={self.outcome!r}, permanent={self.permanent}, "
                f"attempts={self.attempts}, http_status={self.http_status})")


def _clip(s, n):
    s = (s or "").strip()
    return s[:n]


def build_message(token, category, title, body, dry_run=False):
    """Construit le corps HTTP v1. `data` ne contient QUE `type` (allowlist).
    Aucune URL, aucun deep link libre, aucun contenu de consultation."""
    if category not in ALLOWED_TYPES:
        raise ValueError(f"type de notification non autorisé: {category!r}")
    message = {
        "token": token,
        "notification": {
            "title": _clip(title, _TITLE_MAX),
            "body": _clip(body, _BODY_MAX),
        },
        "data": {"type": category},
        "android": {
            "priority": "normal",
            "notification": {
                "channel_id": "auryel_default",
                "visibility": "PRIVATE",
                "default_sound": True,
            },
        },
    }
    payload = {"message": message}
    if dry_run:
        payload["validate_only"] = True
    return payload


def _extract_fcm_error_code(body):
    try:
        err = body.get("error", {})
        for d in err.get("details", []) or []:
            code = d.get("errorCode")
            if code:
                return code
        # repli : certains renvois portent seulement error.status
        return err.get("status")
    except Exception:
        return None


class FcmSender:
    def __init__(self, config, http=None, access_token_provider=None):
        """`http` : objet avec .post(url, headers=, json=, timeout=) -> réponse
        façon requests. `access_token_provider` : callable -> str (jeton OAuth2)."""
        self.config = config
        self._http = http or _default_http()
        self._token_provider = access_token_provider or self._default_token_provider

    # -- obtention du jeton d'accès (google-auth) --------------------------
    def _default_token_provider(self):
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as GoogleAuthRequest
        info = json.loads(self.config.service_account_json)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=[FCM_SCOPE])
        creds.refresh(GoogleAuthRequest())
        return creds.token

    # -- envoi -----------------------------------------------------------
    def send(self, token, category, title, body,
             max_attempts=3, backoff_base=0.5):
        if category not in ALLOWED_TYPES:
            raise ValueError(f"type de notification non autorisé: {category!r}")

        if not self.config.enabled:
            return FcmResult("skipped_disabled")

        if not self.config.configured:
            return FcmResult("config_error",
                             error="FIREBASE_SERVICE_ACCOUNT_JSON / "
                                   "FIREBASE_PROJECT_ID absent")
        try:
            access_token = self._token_provider()
        except Exception as e:
            return FcmResult("config_error",
                             error=f"auth OAuth2 échouée: {type(e).__name__}")

        url = FCM_SEND_URL.format(project_id=self.config.project_id)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }
        payload = build_message(token, category, title, body,
                                dry_run=self.config.dry_run)

        last = None
        for attempt in range(1, max_attempts + 1):
            try:
                resp = self._http.post(url, headers=headers, json=payload,
                                       timeout=self.config.timeout)
            except Exception as e:
                last = FcmResult("transient_error", permanent=False,
                                 error=f"réseau: {type(e).__name__}",
                                 attempts=attempt)
                if attempt < max_attempts:
                    time.sleep(backoff_base * attempt)
                    continue
                return last

            status = getattr(resp, "status_code", None)
            try:
                rbody = resp.json()
            except Exception:
                rbody = {}

            if status == 200:
                if self.config.dry_run:
                    return FcmResult("dry_run", provider_message_id=rbody.get("name"),
                                     attempts=attempt, http_status=200)
                return FcmResult("sent", provider_message_id=rbody.get("name"),
                                 attempts=attempt, http_status=200)

            code = _extract_fcm_error_code(rbody)
            if code in _PERMANENT_FCM_ERRORS:
                return FcmResult("invalid_token", permanent=True,
                                 error=_clip(f"FCM {code}", _ERR_MAX),
                                 attempts=attempt, http_status=status)

            if status in _TRANSIENT_HTTP:
                last = FcmResult("transient_error", permanent=False,
                                 error=_clip(f"HTTP {status} {code or ''}", _ERR_MAX),
                                 attempts=attempt, http_status=status)
                if attempt < max_attempts:
                    time.sleep(backoff_base * attempt)
                    continue
                return last

            # autre 4xx : permanent, mais pas « jeton invalide »
            return FcmResult("permanent_error", permanent=True,
                             error=_clip(f"HTTP {status} {code or ''}", _ERR_MAX),
                             attempts=attempt, http_status=status)

        return last or FcmResult("transient_error", permanent=False,
                                 error="épuisé", attempts=max_attempts)


def _default_http():
    import requests
    return requests
