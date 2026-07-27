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
os.environ.setdefault("TAROT_MEDIA_UPLOAD_DISABLED", "1")

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
        "etat": "normal",
        "onboarding_done": False,
        "relance_j7_envoyee": False,
        "date_premier_contact": (NOW - timedelta(days=2)).isoformat(),
        "date_dernier_contact": (NOW - timedelta(hours=1)).isoformat(),
        "last_morning_message_at": "",
        "last_template_message_at": "",
        "templates_sans_reponse": 0,
        "morning_messages_enabled": True,
        "free_entry_expires_at": "",
        "source_channel": "",
        "nb_echanges": 7,
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

# T1 — Meta Ads actif : free_entry_expires_at valide (72h pas encore écoulées) → free_entry_72h
# Le check free_entry intervient AVANT is_trial_active, onboarding_done n'est pas requis
test(
    "T1  Meta Ads actif (free entry en cours)",
    make_user(
        onboarding_done=True,
        free_entry_expires_at=(NOW + timedelta(hours=48)).isoformat(),
        source_channel="meta_ads",
    ),
    "free_entry_72h",
)

# T2 — TikTok/SEO actif : free_entry_expires_at VIDE, onboarding_done=True, actif < 24h → free_text_24h
# Cas réaliste : prospect SEO/TikTok qui n'a jamais eu de free_entry_expires_at
test(
    "T2  TikTok/SEO actif (fenêtre 24h ouverte, sans free_entry)",
    make_user(
        onboarding_done=True,
        etat="normal",
        free_entry_expires_at="",
        date_dernier_contact=(NOW - timedelta(hours=5)).isoformat(),
        date_premier_contact=(NOW - timedelta(days=2)).isoformat(),
        source_channel="tiktok",
    ),
    "free_text_24h",
)

# T3 — Essai actif J+2 : onboarding_done=True, fenêtre 24h fermée, jours<=2 → paid_template_essai_matin
test(
    "T3  Essai actif J+2 (fenêtre 24h fermée, avant J+3)",
    make_user(
        onboarding_done=True,
        etat="normal",
        free_entry_expires_at="",
        date_dernier_contact=(NOW - timedelta(hours=30)).isoformat(),
        date_premier_contact=(NOW - timedelta(days=2)).isoformat(),
        source_channel="seo",
    ),
    "paid_template_essai_matin",
)

# T4 — J+3 : onboarding_done, etat=attente_paiement (J+2 envoyé), relance_j7 pas encore posée → template conversion
test(
    "T4  J+3 conversion (template obligatoire)",
    make_user(
        onboarding_done=True,
        etat="attente_paiement",
        relance_j7_envoyee=False,
        free_entry_expires_at="",
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

# T11 — Post-essai J+6 : etat=pause, day 6 → premier template réactivation
test(
    "T11 Post-essai J+6 (réactivation)",
    make_user(etat="pause", date_premier_contact=(NOW - timedelta(days=6)).isoformat()),
    "paid_template_reactivation",
)

# T12 — Post-essai J+15 : etat=pause, day 15 → deuxième fenêtre réactivation
test(
    "T12 Post-essai J+15 (réactivation)",
    make_user(etat="pause", date_premier_contact=(NOW - timedelta(days=15)).isoformat()),
    "paid_template_reactivation",
)

# T13 — Post-essai J+30 : etat=pause, day 30 → troisième fenêtre réactivation
test(
    "T13 Post-essai J+30 (réactivation)",
    make_user(etat="pause", date_premier_contact=(NOW - timedelta(days=30)).isoformat()),
    "paid_template_reactivation",
)

# T14 — Post-essai J+5 : etat=pause, day 5 → entre blocage J+3 et fenêtre J+6 → skip
test(
    "T14 Post-essai J+5 (entre blocage et première réactivation)",
    make_user(etat="pause", date_premier_contact=(NOW - timedelta(days=5)).isoformat()),
    "skip_not_subscribed",
)

# T15 — Post-essai J+10, nb_echanges=7 (warm) → fenêtre 10-12 warm → paid_template_reactivation
test(
    "T15 Post-essai J+10 warm (fenêtre 10-12 warm)",
    make_user(etat="pause", date_premier_contact=(NOW - timedelta(days=10)).isoformat()),
    "paid_template_reactivation",
)

# T16 — J+4, relance_j7=True (daily déjà passé), fenêtre fermée → paid_template_matin_j3
test(
    "T16 J+4 relance_j7=True (daily déjà passé) → paid_template_matin_j3",
    make_user(
        onboarding_done=True,
        etat="attente_paiement",
        relance_j7_envoyee=True,
        date_dernier_contact=(NOW - timedelta(hours=30)).isoformat(),
        date_premier_contact=(NOW - timedelta(days=4)).isoformat(),
    ),
    "paid_template_matin_j3",
)

# T17 — Nouveau user sans onboarding → pas de morning message
test(
    "T17 Nouveau user (onboarding_done=False) → skip_not_subscribed",
    make_user(
        onboarding_done=False,
        etat="normal",
        date_premier_contact=(NOW - timedelta(days=1)).isoformat(),
    ),
    "skip_not_subscribed",
)

# T18 — User cold (nb_echanges=3, J+6) → fenêtre 6-8 cold → paid_template_reactivation
test(
    "T18 User cold (nb_echanges=3, J+6) → paid_template_reactivation",
    make_user(
        etat="pause",
        nb_echanges=3,
        date_premier_contact=(NOW - timedelta(days=6)).isoformat(),
    ),
    "paid_template_reactivation",
)

# T19 — User warm (nb_echanges=7, J+6, non-abonné) → template réactivation
test(
    "T19 User warm (nb_echanges=7, J+6) → paid_template_reactivation",
    make_user(
        etat="pause",
        nb_echanges=7,
        date_premier_contact=(NOW - timedelta(days=6)).isoformat(),
    ),
    "paid_template_reactivation",
)

# T20 — Essai J+1 : onboarding_done=True, fenêtre fermée, jours=1 ≤ 2 → paid_template_essai_matin
test(
    "T20 Essai J+1, fenêtre fermée, onboarding=True → paid_template_essai_matin",
    make_user(
        onboarding_done=True,
        etat="normal",
        free_entry_expires_at="",
        date_dernier_contact=(NOW - timedelta(hours=30)).isoformat(),
        date_premier_contact=(NOW - timedelta(days=1)).isoformat(),
    ),
    "paid_template_essai_matin",
)

# T21 — Essai J+2 : onboarding_done=True, fenêtre fermée, jours=2 ≤ 2 → paid_template_essai_matin
test(
    "T21 Essai J+2, fenêtre fermée, onboarding=True → paid_template_essai_matin",
    make_user(
        onboarding_done=True,
        etat="normal",
        free_entry_expires_at="",
        date_dernier_contact=(NOW - timedelta(hours=30)).isoformat(),
        date_premier_contact=(NOW - timedelta(days=2)).isoformat(),
    ),
    "paid_template_essai_matin",
)

# T22 — Essai J+3 : relance_j7=True (daily déjà passé), fenêtre fermée → paid_template_matin_j3
test(
    "T22 Essai J+3, relance_j7=True, fenêtre fermée → paid_template_matin_j3",
    make_user(
        onboarding_done=True,
        etat="normal",
        relance_j7_envoyee=True,
        free_entry_expires_at="",
        date_dernier_contact=(NOW - timedelta(hours=30)).isoformat(),
        date_premier_contact=(NOW - timedelta(days=3)).isoformat(),
    ),
    "paid_template_matin_j3",
)

# T23 — Onboarding non terminé J+2, fenêtre 24h fermée → skip_trial_window_closed
test(
    "T23 onboarding_done=False J+2, fenêtre fermée → skip_trial_window_closed",
    make_user(
        onboarding_done=False,
        abonne=False,
        etat="normal",
        free_entry_expires_at="",
        date_dernier_contact=(NOW - timedelta(hours=30)).isoformat(),
        date_premier_contact=(NOW - timedelta(days=2)).isoformat(),
    ),
    "skip_trial_window_closed",
)

# T24 — Onboarding non terminé J+1, fenêtre 24h fermée → skip_trial_window_closed
test(
    "T24 onboarding_done=False J+1, fenêtre fermée → skip_trial_window_closed",
    make_user(
        onboarding_done=False,
        abonne=False,
        etat="normal",
        free_entry_expires_at="",
        date_dernier_contact=(NOW - timedelta(hours=30)).isoformat(),
        date_premier_contact=(NOW - timedelta(days=1)).isoformat(),
    ),
    "skip_trial_window_closed",
)

# T25 — J+4, etat=pause, onboarding=True, non abonné → paid_template_post_essai_j4
test(
    "T25 J+4, etat=pause, onboarding=True → paid_template_post_essai_j4",
    make_user(
        onboarding_done=True,
        abonne=False,
        etat="pause",
        date_premier_contact=(NOW - timedelta(days=4)).isoformat(),
        date_dernier_contact=(NOW - timedelta(hours=30)).isoformat(),
    ),
    "paid_template_post_essai_j4",
)

# T26 — cold (nb_echanges=3), J+10 : pas dans fenêtre cold (6-8, 15-17, 30-32) → skip
test(
    "T26 cold nb_echanges=3, J+10 → skip_not_subscribed",
    make_user(
        etat="pause",
        nb_echanges=3,
        date_premier_contact=(NOW - timedelta(days=10)).isoformat(),
    ),
    "skip_not_subscribed",
)

# T27 — warm (nb_echanges=7), J+10 : dans fenêtre warm 10-12 → paid_template_reactivation
test(
    "T27 warm nb_echanges=7, J+10 → paid_template_reactivation",
    make_user(
        etat="pause",
        nb_echanges=7,
        date_premier_contact=(NOW - timedelta(days=10)).isoformat(),
    ),
    "paid_template_reactivation",
)

# T28 — hot (nb_echanges=12), J+5 : dans fenêtre hot 5-9 → paid_template_reactivation
test(
    "T28 hot nb_echanges=12, J+5 → paid_template_reactivation",
    make_user(
        etat="pause",
        nb_echanges=12,
        date_premier_contact=(NOW - timedelta(days=5)).isoformat(),
    ),
    "paid_template_reactivation",
)

print()
total = len(results)
passed = sum(results)
all_ok = passed == total
print(f"{'✅' if all_ok else '❌'} {passed}/{total} tests passés")
if not all_ok:
    raise SystemExit(1)
