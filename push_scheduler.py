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
  - méditation : mardi, jeudi et samedi à 19:00 Europe/Paris
  - sommeil                : dimanche 22:00 Europe/Paris
  - bien-être              : 10:00 Europe/Paris (utilisateurs ayant activé le rappel)
  - séance Bien-être       : mardi et samedi à 10:00 Europe/Paris
  - ebook mensuel          : 10:15 Europe/Paris (publication non déjà notifiée)
"""

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from push_fcm import ALLOWED_TYPES
from content_recommendations import follow_up_copy

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
    "wellbeing_daily": (
        "Ton moment Bien-être t'attend",
        "Tes 5 actions du jour sont disponibles dans Auryel.",
    ),
    "ebook_monthly": (
        "Ton nouvel ebook Auryel est disponible",
        "Découvre gratuitement le nouveau guide Bien-être du mois.",
    ),
    "wellbeing_session": (
        "Ta séance Bien-être est prête ✨",
        "Tes 5 exercices du jour t’attendent dans Auryel.",
    ),
}

_ADVISOR_NAMES = {
    "selena": "Séléna", "cassandre": "Cassandre", "maia": "Maïa",
    "luna": "Luna", "ezra": "Ezra", "thea": "Théa", "orion": "Orion",
    "raphael": "Raphaël", "myriam": "Myriam", "kael": "Kaël",
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
        # Une seule notification sommeil hebdomadaire, le dimanche soir.
        self.sleep_days = _parse_days(env.get("PUSH_SLEEP_DAYS"), ("sun",))
        self.meditation_days = _parse_days(
            env.get("PUSH_MEDITATION_DAYS"), ("tue", "thu", "sat"))
        self.wellbeing_time = _parse_hhmm(env.get("PUSH_WELLBEING_TIME"), (10, 0))
        self.session_days = _parse_days(
            env.get("PUSH_WELLBEING_SESSION_DAYS"), ("tue", "sat"))
        self.ebook_time = _parse_hhmm(env.get("PUSH_EBOOK_TIME"), (10, 15))
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
            # La guidance personnelle ciblée est prioritaire sur la pensée
            # éditoriale lorsqu'elle tombe le même jour : le plafond global
            # évite ainsi deux push marketing, sans étouffer une relance
            # directement liée au dernier conseiller utilisé.
            due.append(("personal_guidance", day_key))
            due.append(("daily_thought", day_key))
        if self._is_due(paris_now, self.meditation_days, *self.evening):
            due.append(("daily_meditation", day_key))
        if self._is_due(paris_now, self.sleep_days, *self.sleep_time):
            due.append(("weekly_sleep", f"{week_key}-{paris_now.weekday()}"))
        if self._is_due(paris_now, every_day, *self.wellbeing_time):
            due.append(("wellbeing_daily", day_key))
        if self._is_due(paris_now, self.session_days, *self.wellbeing_time):
            due.append(("wellbeing_session", day_key))
        if self._is_due(paris_now, every_day, *self.ebook_time):
            # Les ebooks sont développés par le store DB : la catégorie ne
            # sera envoyée que pour une publication active non déjà notifiée.
            due.append(("ebook_monthly", day_key))
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
        # Une seule relance éditoriale par jour et par compte. Le rappel
        # Bien-être explicitement configuré reste une exception ; les stores
        # de test/compatibilité sans cette méthode conservent le comportement
        # précédent.
        if category == "ebook_monthly" and hasattr(store, "ebook_jobs"):
            jobs = store.ebook_jobs(now_utc)
        elif category == "wellbeing_daily" and hasattr(store, "wellbeing_jobs"):
            jobs = store.wellbeing_jobs(now_utc)
        elif category == "personal_guidance" and hasattr(store, "personal_guidance_jobs"):
            jobs = store.personal_guidance_jobs(now_utc)
        elif category == "personal_guidance":
            jobs = []
        else:
            users = (store.recipients_for(category, now_utc)
                     if hasattr(store, "recipients_for") else recipients)
            jobs = [{"period": period, "title": MESSAGES[category][0],
                     "body": MESSAGES[category][1], "user_ids": users}]
        for job in jobs:
            job_period = job["period"]
            title, body = job["title"], job["body"]
            user_ids = job.get("user_ids")
            if user_ids is None and hasattr(store, "recipients_for"):
                user_ids = store.recipients_for(category, now_utc)
            for uid in user_ids or []:
                if (category != "wellbeing_daily" and
                        hasattr(store, "global_push_allowed") and
                        not store.global_push_allowed(uid, now_utc)):
                    continue
                dedupe_key = f"{category}:{uid}:{job_period}"
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
                for tok in tokens:
                    data = job.get("data")
                    if data:
                        res = sender.send(tok, category, title, body, data=data)
                    else:
                        # Compatibilité avec les senders de test/intégrations
                        # existants qui ne connaissent que le payload minimal.
                        res = sender.send(tok, category, title, body)
                    if res.outcome in ("sent", "dry_run"):
                        any_ok = True
                    elif res.outcome == "invalid_token":
                        store.mark_invalid(tok)
                if any_ok:
                    store.finalize(dedupe_key, "dry_run" if dry else "sent")
                    if (not dry and category in
                            ("personal_guidance", "wellbeing_session", "ebook_monthly")
                            and hasattr(store, "create_unread_event")):
                        store.create_unread_event(
                            uid, "consultation" if category == "personal_guidance"
                            else "wellbeing", category,
                            str(job.get("ebook_id") or job_period), dedupe_key)
                    if (category == "ebook_monthly" and not dry and
                            hasattr(store, "mark_ebook_notification_sent")):
                        store.mark_ebook_notification_sent(job.get("ebook_id"))
                    if (category == "personal_guidance" and not dry and
                            job.get("recommendation_id") and
                            hasattr(store, "mark_recommendation_followup_sent")):
                        store.mark_recommendation_followup_sent(
                            job.get("recommendation_id"), dedupe_key)
                    summary["sent"] += 1
                else:
                    # Rien parti : libère la clé pour retenter au tick suivant.
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

    def recipients_for(self, category, now_utc):
        """Ciblage métier des rappels utilisateur sans exposer de données.
        Un rappel Bien-être ne part que si le compte l'a explicitement activé.
        Les autres catégories conservent le ciblage historique."""
        if category != "wellbeing_daily":
            return self.recipients()
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute(
                "SELECT DISTINCT d.user_id FROM push_devices d "
                "JOIN accounts a ON a.user_id=d.user_id "
                "JOIN wellbeing_programs w ON w.user_id=a.user_id "
                "WHERE d.enabled=TRUE AND d.revoked_at IS NULL "
                "AND d.invalid_at IS NULL AND a.deleted_at IS NULL "
                "AND w.reminder_enabled=TRUE AND w.reminder_type='wellbeing_daily'"
            )
            return [str(r[0]) for r in c.fetchall()]
        finally:
            conn.close()

    def ebook_jobs(self, now_utc):
        """Retourne les publications actives arrivées à échéance, une fois.
        Les textes viennent du catalogue serveur et la clé de période repose
        sur l'id stable de l'ebook, jamais sur la date du tick."""
        conn = self._get_conn()
        try:
            c = conn.cursor()
            local_date = now_utc.astimezone(PARIS).date()
            c.execute(
                "SELECT id, title, push_type, push_title, push_body, push_active "
                "FROM wellbeing_ebooks "
                "WHERE active=TRUE AND push_active=TRUE "
                "AND push_type='ebook_monthly' AND publication_date<=%s "
                "AND notification_sent_at IS NULL",
                (local_date,),
            )
            rows = c.fetchall()
            users = self.recipients()
            return [{"ebook_id": str(row[0]),
                     "period": f"ebook:{row[0]}",
                     "title": row[3] or MESSAGES["ebook_monthly"][0],
                     "body": row[4] or MESSAGES["ebook_monthly"][1],
                     "user_ids": users} for row in rows]
        finally:
            conn.close()

    def global_push_allowed(self, user_id, now_utc):
        """Plafond éditorial quotidien, évalué par compte.

        Le verrou de déduplication par catégorie reste la protection contre
        les relances identiques ; ce contrôle évite en plus une rafale de
        catégories éditoriales le même jour.
        """
        local_day = now_utc.astimezone(PARIS).date()
        day_start = datetime.combine(local_day, datetime.min.time(), tzinfo=PARIS)
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute(
                "SELECT COUNT(*) FROM notification_sends "
                "WHERE user_id=%s AND category <> 'wellbeing_daily' "
                "AND status IN ('sent', 'dry_run') "
                "AND created_at >= %s",
                (str(user_id), day_start.astimezone(timezone.utc)),
            )
            return int(c.fetchone()[0] or 0) == 0
        finally:
            conn.close()

    def create_unread_event(self, user_id, category, event_type,
                            reference_key, dedupe_key):
        """Crée le badge interne après un envoi FCM confirmé, idempotent."""
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute(
                "INSERT INTO unread_events "
                "(user_id, category, event_type, reference_key, dedupe_key) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (user_id, dedupe_key) DO NOTHING",
                (str(user_id), category, str(event_type)[:80],
                 str(reference_key)[:200], str(dedupe_key)[:240]),
            )
            conn.commit()
        finally:
            conn.close()

    def wellbeing_jobs(self, now_utc):
        """Construit les rappels depuis la préférence persistée de chaque
        programme. Le texte reste donc modifiable côté serveur."""
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute(
                "SELECT w.reminder_text, w.user_id FROM wellbeing_programs w "
                "JOIN accounts a ON a.user_id=w.user_id "
                "JOIN push_devices d ON d.user_id=w.user_id "
                "WHERE w.reminder_enabled=TRUE "
                "AND w.reminder_type='wellbeing_daily' "
                "AND a.deleted_at IS NULL AND d.enabled=TRUE "
                "AND d.revoked_at IS NULL AND d.invalid_at IS NULL"
            )
            grouped = {}
            for body, uid in c.fetchall():
                body = body or MESSAGES["wellbeing_daily"][1]
                grouped.setdefault(body, []).append(str(uid))
            return [{"period": now_utc.astimezone(PARIS).date().isoformat(),
                     "title": MESSAGES["wellbeing_daily"][0],
                     "body": body, "user_ids": users}
                    for body, users in grouped.items()]
        finally:
            conn.close()

    def personal_guidance_jobs(self, now_utc):
        """Relances J+1/J+3/J+5 depuis la dernière activité réelle.

        La consultation et son conseiller sont la seule source de vérité.
        Une consultation encore active n'est jamais relancée. La clé de
        période contient l'instant d'activité, donc toute nouvelle activité
        ouvre automatiquement un nouveau cycle sans réutiliser l'ancien.
        """
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute(
                "SELECT DISTINCT ON (c.user_id) c.user_id, c.advisor_id, "
                "c.last_activity_at FROM consultations c "
                "JOIN accounts a ON a.user_id=c.user_id "
                "JOIN push_devices d ON d.user_id=c.user_id "
                "WHERE c.last_activity_at IS NOT NULL AND a.deleted_at IS NULL "
                "AND d.enabled=TRUE AND d.revoked_at IS NULL AND d.invalid_at IS NULL "
                "ORDER BY c.user_id, c.last_activity_at DESC",
            )
            today = now_utc.astimezone(PARIS).date()
            jobs = []
            for uid, advisor_id, activity in c.fetchall():
                if activity.tzinfo is None:
                    activity = activity.replace(tzinfo=timezone.utc)
                activity_day = activity.astimezone(PARIS).date()
                age = (today - activity_day).days
                if age not in (1, 3, 5):
                    continue
                # Fenêtre active : la dernière activité est encore en cours.
                if now_utc < activity + timedelta(minutes=5):
                    continue
                advisor = str(advisor_id or "").strip().lower()
                name = _ADVISOR_NAMES.get(advisor)
                if not name:
                    continue
                activity_key = activity.astimezone(timezone.utc).isoformat()
                jobs.append({
                    "period": f"guidance:{activity_key}:j{age}",
                    "title": f"{name} aimerait reprendre votre échange ✨",
                    "body": "Une question en tête ? Retrouvez-la dans Auryel.",
                    "user_ids": [str(uid)],
                    "data": {"advisor": advisor},
                })
            # Une relance de contenu reste dans Personal Guidance : même cap,
            # même cooldown, même routage conseiller. V1 ne suit que les ebooks
            # et ne relance pas une personne revenue sur le fil depuis la carte.
            c.execute(
                "SELECT r.id, r.user_id, r.advisor_id, r.title_snapshot, "
                "r.opened_at, r.created_at, r.assistant_message_id "
                "FROM content_recommendations r "
                "JOIN wellbeing_ebooks e ON e.id=r.content_id "
                "JOIN accounts a ON a.user_id=r.user_id "
                "JOIN push_devices d ON d.user_id=r.user_id "
                "WHERE r.content_type='ebook' AND r.follow_up_sent_at IS NULL "
                "AND e.active=TRUE AND e.publication_date<=CURRENT_DATE "
                "AND r.created_at <= %s - INTERVAL '3 days' "
                "AND r.created_at > %s - INTERVAL '4 days' "
                "AND a.deleted_at IS NULL AND d.enabled=TRUE "
                "AND d.revoked_at IS NULL AND d.invalid_at IS NULL "
                "ORDER BY r.created_at ASC",
                (now_utc, now_utc),
            )
            for rec_id, uid, advisor_id, title, opened_at, created_at, assistant_id in c.fetchall():
                advisor = str(advisor_id or "").strip().lower()
                name = _ADVISOR_NAMES.get(advisor)
                if not name:
                    continue
                # Un message utilisateur postérieur à la recommandation signifie
                # que la personne est déjà revenue dans ce fil : pas de relance.
                if assistant_id is not None:
                    c.execute(
                        "SELECT 1 FROM messages m "
                        "JOIN messages rec ON rec.id=%s "
                        "WHERE m.user_id=%s AND m.consultation_id=rec.consultation_id "
                        "AND m.role='user' AND m.id>%s LIMIT 1",
                        (assistant_id, str(uid), assistant_id),
                    )
                    if c.fetchone() is not None:
                        continue
                c.execute(
                    "SELECT 1 FROM consultations c "
                    "JOIN messages rec ON rec.id=%s "
                    "WHERE c.id=rec.consultation_id AND c.last_activity_at>%s "
                    "LIMIT 1",
                    (assistant_id, now_utc - timedelta(minutes=5)),
                ) if assistant_id is not None else None
                if assistant_id is not None and c.fetchone() is not None:
                    continue
                jobs.append({
                    "period": f"content-guidance:{rec_id}:j3",
                    "title": name,
                    "body": follow_up_copy(title, opened_at is not None),
                    "user_ids": [str(uid)],
                    "data": {"advisor": advisor},
                    "recommendation_id": str(rec_id),
                })
            return jobs
        finally:
            conn.close()

    def mark_ebook_notification_sent(self, ebook_id):
        if ebook_id is None:
            return
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute("UPDATE wellbeing_ebooks SET notification_sent_at=NOW() "
                      "WHERE id=%s AND notification_sent_at IS NULL", (ebook_id,))
            conn.commit()
        finally:
            conn.close()

    def mark_recommendation_followup_sent(self, recommendation_id, dedupe_key):
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute(
                "UPDATE content_recommendations SET follow_up_sent_at=NOW(), "
                "follow_up_dedupe_key=%s WHERE id=%s AND follow_up_sent_at IS NULL",
                (str(dedupe_key)[:240], str(recommendation_id)),
            )
            conn.commit()
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
