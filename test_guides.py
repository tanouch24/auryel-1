import sys
from unittest.mock import MagicMock

# Mock psycopg2 avant l'import de auryel_bot (pas de PostgreSQL en local)
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

from datetime import datetime
from auryel_bot import get_system_prompt, GUIDES

USER_BASE = {
    "phone": "+33600000000",
    "prenom": "Sarah",
    "nb_echanges": 8,
    "date_premier_contact": "2026-04-01T10:00:00",
    "date_dernier_contact": "2026-05-10T10:00:00",
    "niveau_attachement": 20,
    "niveau_detresse": 30,
    "theme_dominant": "amour",
    "douleur_principale": "abandon",
    "peur_dominante": "ne pas être aimée",
    "prenoms_importants": "Karim",
    "dernier_sujet_sensible": "",
    "derniere_intention": "",
    "depuis_site": True,
    "abonne": True,
    "guide": "Séraphine",
    "nom_affiche": "Séraphine",
    "email": "",
    "etat": "normal",
}

USER_CRISE = {
    **USER_BASE,
    "niveau_detresse": 100,
    "dernier_sujet_sensible": "suicide",
}

print("=" * 60)
print("TEST — 5 GUIDES")
print("=" * 60)

prompts = {}
for guide in ["Séraphine", "Myriam", "Naomi", "Élias", "Ezra"]:
    user = {**USER_BASE, "guide": guide}
    prompt = get_system_prompt(user, guide)
    prompts[guide] = prompt
    print(f"\n{'─'*40}")
    print(f"GUIDE : {guide}")
    print(f"{'─'*40}")
    print(prompt[:600])

print("\n" + "=" * 60)
print("TEST SÉCURITÉ — cas suicide")
print("=" * 60)
prompt_crise = get_system_prompt(USER_CRISE, "Séraphine")
print(prompt_crise)

print("\n" + "=" * 60)
print("VÉRIFICATIONS AUTOMATIQUES")
print("=" * 60)

valeurs = list(prompts.values())
tous_distincts = len(set(valeurs)) == len(valeurs)
print(f"{'✅' if tous_distincts else '❌'} Prompts distincts : {tous_distincts}")

blocs_interdits = ["TON IDENTITÉ", "Niveau de mysticisme", "guide spirituel sur la plateforme"]
securite_ok = all(bloc not in prompt_crise for bloc in blocs_interdits)
print(f"{'✅' if securite_ok else '❌'} Sécurité sans rôle mystique : {securite_ok}")

print("\n✅ Test terminé.")
