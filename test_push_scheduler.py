"""
test_push_scheduler.py — Fonction métier push_tick (Phase 4).

100 % local : store DB fake en mémoire, FcmSender fake. On vérifie :
  - horaires produits Europe/Paris (pensée 08:30, méditation 19:00,
    sommeil mer+dim 22:00, leçon dim 11:00) ;
  - passage heure d'été / heure d'hiver (l'instant UTC change, l'heure locale
    visée reste 08:30) ;
  - idempotence : deux ticks la même période -> un seul envoi (dedupe_key) ;
  - aucun appareil -> skipped_no_device, pas d'appel FCM ;
  - PUSH_ENABLED=false -> aucun envoi ;
  - échec transitoire -> clé libérée -> retenté au tick suivant ;
  - jeton UNREGISTERED -> marqué invalide.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import push_scheduler as S

_STATE = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _STATE["pass"] += 1
        print(f"OK  {label}")
    else:
        _STATE["fail"] += 1
        print(f"XX  {label}")


PARIS = ZoneInfo("Europe/Paris")


def _utc(y, m, d, hh, mm):
    """datetime UTC-aware."""
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


class _FakeSender:
    def __init__(self, dry_run=False, enabled=True, scripted=None):
        self.config = type("cfg", (), {"dry_run": dry_run, "enabled": enabled})()
        self.calls = []
        self._scripted = scripted or {}   # token -> outcome

    def send(self, token, category, title, body):
        self.calls.append({"token": token, "category": category,
                           "title": title, "body": body})
        outcome = self._scripted.get(token, "dry_run" if self.config.dry_run
                                     else "sent")
        return type("res", (), {"outcome": outcome, "permanent":
                                outcome in ("invalid_token", "permanent_error"),
                                "error": None if outcome in ("sent", "dry_run")
                                else "boom", "provider_message_id": "m"})()


class _FakeStore:
    def __init__(self, users):
        # users: {uid: [tokens]}
        self._users = dict(users)
        self._rows = {}          # dedupe_key -> status
        self.invalidated = []

    def recipients(self):
        return [u for u, toks in self._users.items() if toks]

    def active_tokens(self, uid):
        return list(self._users.get(uid, []))

    def claim(self, uid, category, dedupe_key, status):
        if dedupe_key in self._rows:
            return False
        self._rows[dedupe_key] = status
        return True

    def finalize(self, dedupe_key, status, provider_message_id=None, erreur=None,
                 sent_at=None):
        self._rows[dedupe_key] = status

    def release(self, dedupe_key):
        self._rows.pop(dedupe_key, None)

    def mark_invalid(self, token):
        self.invalidated.append(token)
        for u in self._users:
            self._users[u] = [t for t in self._users[u] if t != token]


SCHED = S.PushSchedule()   # défauts produits


# --- 1. pensée du jour 08:30 Paris (hiver = 07:30 UTC) -------------------
st = _FakeStore({"u1": ["t1"]})
snd = _FakeSender()
summ = S.push_tick(_utc(2026, 1, 15, 7, 35), st, snd, schedule=SCHED)
cats = [c for c, _ in summ["due"]]
check("daily_thought" in cats, "1a — 07:35 UTC (08:35 Paris hiver) -> pensée due")
check(len(snd.calls) == 1 and snd.calls[0]["category"] == "daily_thought",
      "1b — un envoi daily_thought")

# --- 2. AVANT 08:30 -> rien -------------------------------------------------
st = _FakeStore({"u1": ["t1"]})
snd = _FakeSender()
summ = S.push_tick(_utc(2026, 1, 15, 6, 0), st, snd, schedule=SCHED)
check(summ["due"] == [], "2 — 07:00 Paris -> aucune catégorie due")

# --- 3. DST : 08:30 Paris en été = 06:30 UTC ---------------------------
st = _FakeStore({"u1": ["t1"]})
snd = _FakeSender()
summ_summer = S.push_tick(_utc(2026, 7, 15, 6, 35), st, snd, schedule=SCHED)
check("daily_thought" in [c for c, _ in summ_summer["due"]],
      "3a — 06:35 UTC en été = 08:35 Paris -> pensée due")
st2 = _FakeStore({"u1": ["t1"]})
snd2 = _FakeSender()
summ_winter_at_0635 = S.push_tick(_utc(2026, 1, 15, 6, 35), st2, snd2,
                                  schedule=SCHED)
check("daily_thought" not in [c for c, _ in summ_winter_at_0635["due"]],
      "3b — 06:35 UTC en hiver = 07:35 Paris -> PAS encore due")

# --- 4. idempotence : 2e tick même jour -> dédupe -----------------------
st = _FakeStore({"u1": ["t1"]})
snd = _FakeSender()
S.push_tick(_utc(2026, 1, 15, 7, 35), st, snd, schedule=SCHED)
summ2 = S.push_tick(_utc(2026, 1, 15, 9, 0), st, snd, schedule=SCHED)
check(len(snd.calls) == 1, "4a — 2e tick le même jour -> aucun nouvel envoi")
check(summ2["deduped"] >= 1, "4b — 2e tick compte un dédupe")

# --- 5. aucun appareil -> skipped_no_device --------------------------
st = _FakeStore({"u1": []})
snd = _FakeSender()
summ = S.push_tick(_utc(2026, 1, 15, 7, 35), st, snd, schedule=SCHED)
check(snd.calls == [], "5 — aucun device -> aucun appel FCM")

# --- 6. méditation 19:00, sommeil dim 22:00, leçon dim 11:00 ---------
# 2026-01-18 = dimanche
st = _FakeStore({"u1": ["t1"]})
snd = _FakeSender()
summ = S.push_tick(_utc(2026, 1, 18, 21, 5), st, snd, schedule=SCHED)  # 22:05 Paris dim
cats = [c for c, _ in summ["due"]]
check("weekly_sleep" in cats, "6a — dimanche 22:05 Paris -> sommeil dû")
check("weekly_life_lesson" not in cats, "6b — 22:05 -> leçon (11:00) pas re-due tardivement")
st = _FakeStore({"u1": ["t1"]})
snd = _FakeSender()
summ = S.push_tick(_utc(2026, 1, 17, 21, 5), st, snd, schedule=SCHED)  # samedi
check("weekly_sleep" not in [c for c, _ in summ["due"]],
      "6c — samedi 22:05 -> sommeil PAS dû (mer+dim seulement)")

# --- 7. PUSH_ENABLED=false via sender -> aucun envoi -----------------
st = _FakeStore({"u1": ["t1"]})
snd = _FakeSender(enabled=False)
summ = S.push_tick(_utc(2026, 1, 15, 7, 35), st, snd, schedule=SCHED)
check(summ["sent"] == 0, "7 — sender désactivé -> 0 envoi")

# --- 8. échec transitoire -> clé libérée -> retenté ------------------
st = _FakeStore({"u1": ["t1"]})
snd = _FakeSender(scripted={"t1": "transient_error"})
S.push_tick(_utc(2026, 1, 15, 7, 35), st, snd, schedule=SCHED)
check(len(snd.calls) == 1, "8a — 1er tick a tenté l'envoi")
snd.calls.clear()
snd._scripted = {}   # le service refonctionne
S.push_tick(_utc(2026, 1, 15, 8, 30), st, snd, schedule=SCHED)
check(len(snd.calls) == 1, "8b — échec transitoire -> retenté au tick suivant")

# --- 9. UNREGISTERED -> token marqué invalide -----------------------
st = _FakeStore({"u1": ["t1", "t2"]})
snd = _FakeSender(scripted={"t1": "invalid_token"})
S.push_tick(_utc(2026, 1, 15, 7, 35), st, snd, schedule=SCHED)
check("t1" in st.invalidated, "9 — jeton UNREGISTERED marqué invalide")

# --- 10. catch-up borné : tick 6h après l'heure -> encore envoyé, 8h -> non
st = _FakeStore({"u1": ["t1"]})
snd = _FakeSender()
summ = S.push_tick(_utc(2026, 1, 15, 13, 0), st, snd, schedule=SCHED)  # 14:00 Paris
check("daily_thought" in [c for c, _ in summ["due"]],
      "10a — 5h30 de retard -> encore due (catch-up)")
st = _FakeStore({"u1": ["t1"]})
snd = _FakeSender()
summ = S.push_tick(_utc(2026, 1, 15, 16, 0), st, snd, schedule=SCHED)  # 17:00 Paris
check("daily_thought" not in [c for c, _ in summ["due"]],
      "10b — 8h30 de retard -> plus due (hors fenêtre catch-up)")

print(f"\n{_STATE['pass']} OK / {_STATE['fail']} XX")
raise SystemExit(1 if _STATE["fail"] else 0)
