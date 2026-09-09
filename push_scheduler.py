"""
push_scheduler.py — Fonction métier `push_tick` des notifications programmées.

Conçue pour être déclenchée par un PLANIFICATEUR EXTERNE (Railway Cron) via
POST /cron/push-tick, ou une commande job — PAS par APScheduler dans le
process web (redémarrages Railway, déploiements, sommeil du service, risque de
doublon multi-worker).

Le tick peut s'exécuter TRÈS souvent : il décide lui-même si un envoi est dû
et l'idempotence est garantie par `notification_sends.dedupe_key` (UNIQUE).

Tous les instants persistés en UTC. Le calcul métier applique
ZoneInfo("Europe/Paris") (heure d'été / heure d'hiver gérées par zoneinfo).

Horaires produits par défaut (surchargeables par variables d'env, valeurs
non secrètes) :
  - pensée quotidienne     : 08:30 Europe/Paris, tous les jours
  - méditation quotidienne : 19:00 Europe/Paris, tous les jours
  - sommeil                : mercredi & dimanche 22:00 Europe/Paris
  - leçon de vie           : dimanche 11:00 Europe/Paris
"""

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from push_fcm import ALLOWED_TYPES

PARIS = ZoneInfo("Europe/Paris")

# Titres / corps GÉNÉRIQUES, sûrs sur écran verrouillé. Jamais de contenu de
# consultation, jamais de donnée personnelle.
MESSAGES = {
    "daily_thought": (
        "Ta pensée du jour t'attend",
        "Ouvre Auryel pour la découvrir.",
    ),
    "daily_meditation": (
        "Ton moment du soir",
        "Une méditation t'attend dans Auryel.",
    ),
    "weekly_sleep": (
        "Besoin de décrocher avant de dormir ?",
        "Prends un instant pour toi dans Auryel.",
    ),
    "weekly_life_lesson": (
        "Ta leçon de vie de la semaine",
        "Elle t'attend dans Auryel.",
    ),
}

_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _parse_hhmm(value, default):
    try:
        hh, mm = str(value).strip().split(":")
        hh, mm = int(hh), int(mm)
        if 0 <= hh < 24 and 0 <= mm < 60:
            return hh, mm
    except Exception:
        pass
    hh, mm = default
    return hh, mm


def _parse_days(value, default):
    out = []
    for tok in str(value or "").lower().replace(" ", "").split(","):
        if tok in _WEEKDAYS:
            out.append(_WEEKDAYS[tok])
    return out or [_WEEKDAYS[d] for d in default]


class PushSchedule:
    """Horaires résolus. `catch_up_hours` : au-delà de ce retard, on n'envoie
    plus (évite une « pensée du jour » à 23 h après une panne)."""

    def __init__(self, env=None, catch_up_hours=6):
        env = env or {}
        self.morning = _parse_hhmm(env.get("PUSH_TIME_MORNING"), (8, 30))
        self.evening = _parse_hhmm(env.get("PUSH_TIME_EVENING"), (19, 0))
        self.sleep_time = _parse_hhmm(env.get("PUSH_SLEEP_TIME"), (22, 0))
        self.sleep_days = _parse_days(env.get("PUSH_SLEEP_DAYS"), ("wed", "sun"))
        self.lesson_time = _parse_hhmm(env.get("PUSH_LESSON_TIME"), (11, 0))
        self.lesson_days = _parse_days(env.get("PUSH_LESSON_DAY"), ("sun",))
        self.catch_up_hours = catch_up_hours

    @classmethod
    def from_env(cls, env=None):
        return cls(env or os.environ)

    # -- calcul de ce qui est dû ------------------------------------------
    def _is_due(self, paris_now, target_days, hh, mm):
        """True si, aujourd'hui (jour local autorisé), l'heure locale est
        comprise entre l'heure cible et heure cible + catch_up_hours."""
        if paris_now.weekday() not in target_days:
            return False
        target = paris_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if paris_now < target:
            return False
        return paris_now - target <= timedelta(hours=self.catch_up_hours)

    def due_categories(self, paris_now):
        """Liste de (category, period_key) dûs à `paris_now` (aware Europe/Paris).
        period_key sert de suffixe au dedupe_key (une seule fois par période)."""
        iso = paris_now.isocalendar()
        day_key = paris_now.date().isoformat()
        week_key = f"{iso[0]}-W{iso[1]:02d}"
        due = []
        every_day = list(range(7))
        if self._is_due(paris_now, every_day, *self.morning):
            due.append(("daily_thought", day_key))
        if self._is_due(paris_now, every_day, *self.evening):
            due.append(("daily_meditation", day_key))
        if self._is_due(paris_now, self.sleep_days, *self.sleep_time):
            due.append(("weekly_sleep", f"{week_key}-{paris_now.weekday()}"))
        if self._is_due(paris_now, self.lesson_days, *self.lesson_time):
            due.append(("weekly_life_lesson", week_key))
        return due


def push_tick(now_utc, store, sender, schedule=None):
    """Un « battement » : envoie les notifications dues à `now_utc` (aware UTC).

    `store`  : objet DB (recipients / active_tokens / claim / finalize /
               release / mark_invalid) — cf. DbPushTickStore.
    `sender` : push_fcm.FcmSender (ou fake).
    Retour : dict résumé (aucun jeton, aucune donnée perso)."""
    schedule = schedule or PushSchedule.from_env()
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    paris_now = now_utc.astimezone(PARIS)
    due = schedule.due_categories(paris_now)

    summary = {
        "ticked_at": now_utc.astimezone(timezone.utc).isoformat(),
        "due": due,
        "sent": 0, "skipped_no_device": 0, "failed": 0, "deduped": 0,
    }
    if not due:
        return summary

    dry = bool(getattr(sender.config, "dry_run", False))
    enabled = bool(getattr(sender.config, "enabled", True))
    recipients = store.recipients()

    for category, period in due:
        if category not in ALLOWED_TYPES or category not in MESSAGES:
            continue
        title, body = MESSAGES[category]
        for uid in recipients:
            dedupe_key = f"{category}:{uid}:{period}"
            tokens = store.active_tokens(uid)
            provisional = ("dry_run" if dry
                           else "sent" if tokens
                           else "skipped_no_device")
            if not store.claim(uid, category, dedupe_key, provisional):
                summary["deduped"] += 1
                continue
            if not enabled:
                store.finalize(dedupe_key, "skipped_disabled")
                continue
            if not tokens:
                summary["skipped_no_device"] += 1
                continue

            any_ok = False
            last_err = None
            for tok in tokens:
                res = sender.send(tok, category, title, body)
                if res.outcome in ("sent", "dry_run"):
                    any_ok = True
                elif res.outcome == "invalid_token":
                    store.mark_invalid(tok)
                else:
                    last_err = res.error
            if any_ok:
                store.finalize(dedupe_key, "dry_run" if dry else "sent")
                summary["sent"] += 1
            else:
                # rien parti : on LIBÈRE la clé pour retenter au prochain tick
                # (tant qu'on reste dans la fenêtre catch-up).
                store.release(dedupe_key)
                summary["failed"] += 1

    return summary


# --------------------------------------------------------------------------
# Implémentation DB réelle du store (utilisée par /cron/push-tick).
# Importée paresseusement de auryel_bot pour éviter un cycle à l'import.
# --------------------------------------------------------------------------
class DbPushTickStore:
    def __init__(self, get_conn, uuid_mod=None):
        self._get_conn = get_conn
        import uuid as _uuid
        self._uuid = uuid_mod or _uuid

    def recipients(self):
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute(
                "SELECT DISTINCT d.user_id FROM push_devices d "
                "JOIN accounts a ON a.user_id = d.user_id "
                "WHERE d.enabled = TRUE AND d.revoked_at IS NULL "
                "AND d.invalid_at IS NULL AND a.deleted_at IS NULL"
            )
            return [str(r[0]) for r in c.fetchall()]
        finally:
            conn.close()

    def active_tokens(self, user_id):
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute(
                "SELECT fcm_token FROM push_devices "
                "WHERE user_id=%s AND enabled=TRUE "
                "AND revoked_at IS NULL AND invalid_at IS NULL",
                (str(user_id),),
            )
            return [r[0] for r in c.fetchall()]
        finally:
            conn.close()

    def claim(self, user_id, category, dedupe_key, status):
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute(
                "INSERT INTO notification_sends "
                "(id, user_id, category, dedupe_key, status, created_at) "
                "VALUES (%s, %s, %s, %s, %s, NOW()) "
                "ON CONFLICT (dedupe_key) DO NOTHING",
                (str(self._uuid.uuid4()), str(user_id), category, dedupe_key,
                 status),
            )
            conn.commit()
            return c.rowcount == 1
        finally:
            conn.close()

    def finalize(self, dedupe_key, status, provider_message_id=None,
                 erreur=None, sent_at=None):
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute(
                "UPDATE notification_sends SET status=%s, "
                "provider_message_id=%s, erreur=%s, "
                "sent_at=COALESCE(%s, sent_at) WHERE dedupe_key=%s",
                (status, provider_message_id,
                 (erreur or None) if erreur is None else str(erreur)[:200],
                 sent_at, dedupe_key),
            )
            conn.commit()
        finally:
            conn.close()

    def release(self, dedupe_key):
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute("DELETE FROM notification_sends WHERE dedupe_key=%s "
                      "AND status <> 'sent'", (dedupe_key,))
            conn.commit()
        finally:
            conn.close()

    def mark_invalid(self, token):
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute(
                "UPDATE push_devices SET enabled=FALSE, invalid_at=NOW(), "
                "updated_at=NOW() WHERE fcm_token=%s AND invalid_at IS NULL",
                (token,),
            )
            conn.commit()
        finally:
            conn.close()
