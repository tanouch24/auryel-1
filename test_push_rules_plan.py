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


def test_guides_for_casts_uuid_to_text():
    # Régression prod 09/10/2026 : « operator does not exist: uuid = text ».
    import inspect
    import push_scheduler as ps
    src = inspect.getsource(ps.DbPushTickStore.guides_for)
    assert "user_id::text = ANY(%s)" in src


def test_distress_guard_reads_text_scores_from_app_profiles():
    # Prod 09/10/2026 : niveau_detresse stocké en TEXTE dans app_profiles ;
    # TypeError -> fail-safe -> AUCUNE push envoyée à personne.
    import os
    for k, v in {"SECRET_KEY": "t", "ADMIN_PASSWORD": "t",
                 "DATABASE_URL": "postgresql://t:t@127.0.0.1:1/t"}.items():
        os.environ.setdefault(k, v)
    import auryel_bot as A
    calm = {"niveau_detresse": "3", "detresse_maj_at": "2026-10-09T10:00:00",
            "dernier_signal_aigu_at": ""}
    assert A._detresse_bloque_marketing(calm) == (False, None)
    assert A._detresse_bloque_marketing(A._app_profile_to_user_dict(calm)) == (False, None)
    high = dict(calm, niveau_detresse="95", detresse_maj_at="")
    assert A._detresse_bloque_marketing(high) == (True, "score")

    store = ps.DbPushTickStore(lambda: None)
    real = A.get_app_profile
    try:
        A.get_app_profile = lambda uid: calm
        assert store.push_blocked_for_distress("u", MORNING) is False
        A.get_app_profile = lambda uid: high
        assert store.push_blocked_for_distress("u", MORNING) is True
    finally:
        A.get_app_profile = real
