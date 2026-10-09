"""Relances « offre exceptionnelle » (09/10/2026) : J+2/6/13/30/60/90 après la
fin des minutes offertes, 19:00 Paris, hors plafond quotidien, jamais en
détresse, ouvrent la page Premium (data.type = premium_offer)."""
from datetime import datetime, timezone
from types import SimpleNamespace

import push_fcm
import push_scheduler as ps


class _Sender:
    def __init__(self):
        self.config = SimpleNamespace(dry_run=False, enabled=True)
        self.sent = []

    def send(self, token, category, title, body, data=None):
        self.sent.append((token, category, title, body, data))
        return SimpleNamespace(outcome="sent")


class _Store:
    def __init__(self, offer_users, distress=(), cap_reached=()):
        self.offer_users = offer_users
        self.distress = set(distress)
        self.cap_reached = set(cap_reached)
        self.claimed = set()

    def recipients(self):
        return []

    def recipients_for(self, category, now):
        return []

    def personal_guidance_jobs(self, now):
        return []

    def premium_offer_jobs(self, now):
        return [{"period": "offer:j2", "title": "Offre exceptionnelle 🌙",
                 "body": "x", "user_ids": [u], "data": {"advisor": "selena"}}
                for u in self.offer_users]

    def push_blocked_for_distress(self, uid, now):
        return uid in self.distress

    def global_push_allowed(self, uid, now):
        return uid not in self.cap_reached

    def active_tokens(self, uid):
        return [f"tok-{uid}"]

    def claim(self, uid, category, key, status):
        if key in self.claimed:
            return False
        self.claimed.add(key)
        return True

    def finalize(self, key, status):
        pass

    def release(self, key):
        pass

    def mark_invalid(self, tok):
        pass


# 19:05 Europe/Paris (heure d'été) : offre due.
EVENING = datetime(2026, 9, 17, 17, 5, tzinfo=timezone.utc)


def test_offer_is_due_at_19h_paris_only():
    sched = ps.PushSchedule({})
    assert ("premium_offer", "2026-09-17") in sched.due_categories(
        EVENING.astimezone(ps.PARIS))
    morning = datetime(2026, 9, 17, 6, 35, tzinfo=timezone.utc)
    assert not any(c == "premium_offer"
                   for c, _ in sched.due_categories(morning.astimezone(ps.PARIS)))


def test_offer_is_an_allowed_fcm_type():
    assert "premium_offer" in push_fcm.ALLOWED_TYPES
    msg = push_fcm.build_message("t", "premium_offer", "a", "b", data={"advisor": "selena"})
    assert msg["message"]["data"]["type"] == "premium_offer"


def test_offer_ignores_daily_cap_but_never_distress():
    sender = _Sender()
    summary = ps.push_tick(
        EVENING, _Store(["ok", "capped", "fragile"], distress=["fragile"],
                        cap_reached=["capped"]), sender)
    toks = {s[0] for s in sender.sent if s[1] == "premium_offer"}
    assert toks == {"tok-ok", "tok-capped"}
    assert summary["blocked_distress"] == 1
    assert summary["candidates"] == 3


def test_offer_sent_once_per_step():
    store, sender = _Store(["u1"]), _Sender()
    ps.push_tick(EVENING, store, sender)
    ps.push_tick(EVENING, store, sender)
    assert len([s for s in sender.sent if s[1] == "premium_offer"]) == 1


def test_offer_calendar_and_copy():
    assert sorted(ps.PREMIUM_OFFER_STEPS) == [2, 6, 13, 30, 60, 90]
    for title, body in ps.PREMIUM_OFFER_STEPS.values():
        text = (title + body).format(name="Séléna")
        assert "4,99" in text
        assert "Dernier" not in text  # aucune fausse dernière chance
    j2 = ps.PREMIUM_OFFER_STEPS[2][1].format(name="Séléna")
    assert "29,99 €" in j2 and "4 h avec Séléna" in j2


class _Cur:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=()):
        self.sql = sql

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return _Cur(self.rows)

    def close(self):
        pass


def test_db_jobs_pick_only_the_steps_and_sign_with_the_guide():
    def ended(days_ago):
        return datetime(2026, 9, 17 - days_ago, 15, 0, tzinfo=timezone.utc) \
            if days_ago < 17 else datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)
    rows = [("u-j2", ended(2), "selena"), ("u-j3", ended(3), "luna"),
            ("u-j30", ended(30), "orion"), ("u-noguide", ended(6), None)]
    store = ps.DbPushTickStore(lambda: _Conn(rows))
    jobs = {j["user_ids"][0]: j for j in store.premium_offer_jobs(EVENING)}
    assert set(jobs) == {"u-j2", "u-j30", "u-noguide"}
    assert jobs["u-j2"]["period"] == "offer:j2"
    assert "Séléna" in jobs["u-j2"]["body"]
    assert jobs["u-j30"]["title"] == "Orion pense à toi ✨"
    assert jobs["u-noguide"]["title"] == "Ton guide t’attend"
    assert jobs["u-noguide"]["data"] == {}


# --- Notification « question offerte » (J+4 / J+20, 13:00) ------------------

def test_gift_question_due_at_13h_and_allowed():
    noon = datetime(2026, 9, 17, 11, 5, tzinfo=timezone.utc)  # 13:05 Paris
    due = ps.PushSchedule({}).due_categories(noon.astimezone(ps.PARIS))
    assert ("gift_question", "2026-09-17") in due
    assert "gift_question" in push_fcm.ALLOWED_TYPES
    assert sorted(ps.GIFT_QUESTION_STEPS) == [4, 20]


def test_gift_question_jobs_use_their_own_steps():
    rows = [("u-j4", datetime(2026, 9, 13, 15, 0, tzinfo=timezone.utc), "luna"),
            ("u-j2", datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc), "luna")]
    store = ps.DbPushTickStore(lambda: _Conn(rows))
    jobs = store.premium_offer_jobs(EVENING, ps.GIFT_QUESTION_STEPS, "gift")
    assert [j["user_ids"][0] for j in jobs] == ["u-j4"]
    assert jobs[0]["period"] == "gift:j4"
    assert jobs[0]["title"] == "🎁 Luna t’a réservé un tirage"
