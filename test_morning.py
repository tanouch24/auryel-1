import sys
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

import os
os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("VERIFY_TOKEN", "test")
os.environ.setdefault("ADMIN_PASSWORD", "test")
os.environ.setdefault("CRON_SECRET", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("STRIPE_SK", "sk_test_placeholder")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_placeholder")
os.environ.setdefault("WHATSAPP_TOKEN", "test")
os.environ.setdefault("PHONE_NUMBER_ID", "test")
os.environ.setdefault("GROQ_API_KEY", "test")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("META_APP_SECRET", "test")
os.environ.setdefault("DAILY_SECRET", "test")
os.environ.setdefault("SEO_SECRET", "test")

from datetime import datetime, timedelta
from auryel_bot import choose_morning_send_mode

# "Maintenant" fixe pour tous les tests
NOW = datetime(2026, 6, 19, 9, 0, 0)

def make_user(**kwargs):
    base = {
        "phone": "+33600000000",
        "prenom": "Sarah",
        "abonne": False,
        "stop_relances": False,
        "date_premier_contact": (NOW - timedelta(days=2)).isoformat(),
        "date_dernier_contact": (NOW - timedelta(hours=1)).isoformat(),
        "last_morning_message_at": "",
        "last_template_message_at": "",
        "templates_sans_reponse": 0,
        "morning_messages_enabled": True,
        "free_entry_expires_at": "",
        "source_channel": "",
    }
    base.update(kwargs)
    return base

results = []

def test(label, user, expected):
    result = choose_morning_send_mode(user, NOW)
    ok = result == expected
    results.append(ok)
    status = "✅" if ok else "❌"
    print(f"{status} {label}")
    if not ok:
        print(f"   attendu : {expected!r}")
        print(f"   obtenu  : {result!r}")

print("=" * 60)
print("TEST MORNING — choose_morning_send_mode()")
print("=" * 60)

# T1 — Meta Ads actif : free_entry encore valide → free_entry_72h
test(
    "T1  Meta Ads actif (free entry en cours)",
    make_user(
        free_entry_expires_at=(NOW + timedelta(hours=48)).isoformat(),
        source_channel="meta_ads",
    ),
    "free_entry_72h",
)

# T2 — TikTok/SEO actif : free_entry expiré, user actif < 24h → free_text_24h
test(
    "T2  TikTok/SEO actif (fenêtre 24h ouverte)",
    make_user(
        free_entry_expires_at=(NOW - timedelta(hours=2)).isoformat(),
        date_dernier_contact=(NOW - timedelta(hours=5)).isoformat(),
        date_premier_contact=(NOW - timedelta(days=4)).isoformat(),
        source_channel="tiktok",
    ),
    "free_text_24h",
)

# T3 — TikTok/SEO silencieux : free_entry expiré, pas actif 24h, pas jour 3 → skip
test(
    "T3  TikTok/SEO silencieux (fenêtre fermée, pas J+3)",
    make_user(
        free_entry_expires_at=(NOW - timedelta(hours=2)).isoformat(),
        date_dernier_contact=(NOW - timedelta(hours=30)).isoformat(),
        date_premier_contact=(NOW - timedelta(days=5)).isoformat(),
        source_channel="seo",
    ),
    "skip_trial_window_closed",
)

# T4 — J+3 : free_entry expiré, pas actif 24h, exactement jour 3 → template conversion
test(
    "T4  J+3 conversion (template obligatoire)",
    make_user(
        free_entry_expires_at=(NOW - timedelta(hours=2)).isoformat(),
        date_dernier_contact=(NOW - timedelta(hours=30)).isoformat(),
        date_premier_contact=(NOW - timedelta(days=3)).isoformat(),
        source_channel="tiktok",
    ),
    "paid_template_j3_conversion",
)

# T5 — Abonné actif : abonne=True, date_dernier_contact < 24h → free_text_24h
test(
    "T5  Abonné actif (message libre)",
    make_user(
        abonne=True,
        date_dernier_contact=(NOW - timedelta(hours=3)).isoformat(),
        templates_sans_reponse=5,
    ),
    "free_text_24h",
)

# T6 — Abonné silencieux : abonne=True, pas actif 24h, templates < 10 → template matin
test(
    "T6  Abonné silencieux (template matin)",
    make_user(
        abonne=True,
        date_dernier_contact=(NOW - timedelta(hours=30)).isoformat(),
        templates_sans_reponse=3,
        last_template_message_at=(NOW - timedelta(days=1)).isoformat(),
    ),
    "paid_template_abonne_matin",
)

# T7 — Abonné 10 templates, dernier template récent → skip budget
test(
    "T7  Abonné 10 templates (budget limit, dernier template récent)",
    make_user(
        abonne=True,
        date_dernier_contact=(NOW - timedelta(hours=30)).isoformat(),
        templates_sans_reponse=10,
        last_template_message_at=(NOW - timedelta(days=1)).isoformat(),
    ),
    "skip_budget_limit",
)

# T8 — STOP : stop_relances=True → skip_stop (priorité absolue)
test(
    "T8  STOP opt-out (priorité absolue)",
    make_user(
        stop_relances=True,
        abonne=True,
        date_dernier_contact=(NOW - timedelta(hours=1)).isoformat(),
    ),
    "skip_stop",
)

# T9 — Abonné silencieux, 10 templates mais > 3 jours → slowed_down
test(
    "T9  Abonné silencieux long (ralenti après budget limit)",
    make_user(
        abonne=True,
        date_dernier_contact=(NOW - timedelta(hours=30)).isoformat(),
        templates_sans_reponse=10,
        last_template_message_at=(NOW - timedelta(days=5)).isoformat(),
    ),
    "slowed_down_template",
)

# T10 — Déjà envoyé aujourd'hui → skip immédiat avant tout le reste
test(
    "T10 Déjà envoyé aujourd'hui (idempotence)",
    make_user(
        abonne=True,
        last_morning_message_at=(NOW - timedelta(hours=1)).isoformat(),
    ),
    "skip_already_sent_today",
)

print()
total = len(results)
passed = sum(results)
all_ok = passed == total
print(f"{'✅' if all_ok else '❌'} {passed}/{total} tests passés")
if not all_ok:
    raise SystemExit(1)
