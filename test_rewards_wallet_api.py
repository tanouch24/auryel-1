"""Contrat actif de GET rewards/wallet après migration Rewarded V1.

Le wallet historique Stars reste en base mais ne doit plus être lu par cette
route. Le test utilise une DB locale simulée et ne touche aucun service.
"""

import os
import sys
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()
for key, value in {
    "SECRET_KEY": "test-secret-key", "VERIFY_TOKEN": "test",
    "ADMIN_PASSWORD": "test", "DATABASE_URL": "postgresql://test:test@localhost/test",
    "STRIPE_SK": "sk_test", "STRIPE_WEBHOOK_SECRET": "whsec_test",
    "WHATSAPP_TOKEN": "test", "PHONE_NUMBER_ID": "test", "GROQ_API_KEY": "test",
    "RESEND_API_KEY": "test", "META_APP_SECRET": "test", "DAILY_SECRET": "test",
}.items():
    os.environ.setdefault(key, value)

import auryel_bot as A

UID = "11111111-1111-4111-8111-111111111111"
DB = {UID: (3, 7, 17, 5)}


class Cursor:
    def __init__(self):
        self.row = None

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "FROM accounts" in normalized:
            self.row = (params[0],) if params[0] == UID else None
        elif "FROM rewarded_entitlements" in normalized:
            self.row = DB.get(params[0])
        else:
            raise AssertionError("SQL inattendu pour le contrat Rewarded V1: " + normalized)

    def fetchone(self):
        return self.row

    def close(self):
        pass


class Conn:
    def cursor(self):
        return Cursor()

    def close(self):
        pass


A.get_conn = Conn
A.app.config["TESTING"] = True
A.resolve_app_session = lambda token: {
    "session_id": "s", "user_id": UID, "email": "test@example.com"
} if token else None
client = A.app.test_client()
headers = {"Authorization": "Bearer test"}


def check(condition, label):
    if not condition:
        raise AssertionError(label)
    print("OK ", label)


rules = {(rule.rule, method) for rule in A.app.url_map.iter_rules()
         for method in rule.methods}
check(("/api/app/rewards/wallet", "GET") in rules,
      "route wallet Rewarded V1 présente")
check(("/api/app/rewards/claim", "POST") in rules,
      "route claim legacy conservée pour compatibilité")

response = client.get("/api/app/rewards/wallet", headers=headers)
body = response.get_json()
check(response.status_code == 200, "wallet retourne 200")
check(body == {
    "questions_available": 3,
    "progress": 7,
    "total_rewarded": 17,
    "minutes_awarded": 5,
    "next_minutes_at": 3,
}, "wallet expose uniquement l’état Rewarded autoritaire")
check("stars_balance" not in body, "aucun solde Stars dans l’API active")
check("express_products" not in body, "aucun achat Express Stars dans l’API active")

claim = client.post("/api/app/rewards/claim", json={"action_key": "wake_completed"},
                    headers=headers)
check(claim.status_code == 410, "claim historique désactivé")
check(claim.get_json()["reason"] == "reward_claims_disabled",
      "claim ne crédite plus de récompense utilisateur")
print("RESULTAT : 7/7 tests Rewarded wallet passés")
