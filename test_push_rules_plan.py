"""Règles push du plan produit (08/10/2026) : détresse, signature du guide."""
from datetime import datetime, timezone
from types import SimpleNamespace

import push_scheduler as ps


class _Sender:
    def __init__(self):
        self.config = SimpleNamespace(dry_run=False, enabled=True)
        self.sent = []

    def send(self, token, category, title, body, data=None):
        self.sent.append((token, category, title, body))
        return SimpleNamespace(outcome="sent")


class _Store:
    def __init__(self, users, distress=(), guides=None):
        self.users = users
        self.distress = set(distress)
        self.guides = guides or {}
        self.claimed = set()

    def recipients(self):
        return list(self.users)

    def recipients_for(self, category, now):
        return list(self.users)

    def personal_guidance_jobs(self, now):
        return []

    def push_blocked_for_distress(self, uid, now):
        return uid in self.distress

    def guides_for(self, user_ids):
        return {u: self.guides.get(u, "") for u in user_ids}

    def global_push_allowed(self, uid, now):
        return True

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


# 08:35 Europe/Paris un jeudi : pensée du jour due.
MORNING = datetime(2026, 9, 17, 6, 35, tzinfo=timezone.utc)


def test_distress_blocks_every_notification():
    sender = _Sender()
    ps.push_tick(MORNING, _Store(["ok", "fragile"], distress=["fragile"]), sender)
    assert {tok for tok, *_ in sender.sent} == {"tok-ok"}


def test_morning_notification_is_signed_by_the_users_guide():
    sender = _Sender()
    ps.push_tick(MORNING, _Store(["u1", "u2"], guides={"u1": "luna"}), sender)
    by_tok = {tok: (title, body) for tok, cat, title, body in sender.sent
              if cat == "daily_thought"}
    assert by_tok["tok-u1"] == ("Luna ✨", ps.GUIDE_MORNING_BODY)
    # Guide inconnu : texte générique conservé, jamais de prénom inventé.
    assert by_tok["tok-u2"] == ps.MESSAGES["daily_thought"]


def test_distress_lookup_error_fails_safe():
    store = ps.DbPushTickStore(lambda: None)
    import auryel_bot as bot
    original = bot.get_app_profile
    bot.get_app_profile = lambda uid: (_ for _ in ()).throw(RuntimeError("db"))
    try:
        assert store.push_blocked_for_distress("u1", MORNING) is True
    finally:
        bot.get_app_profile = original
