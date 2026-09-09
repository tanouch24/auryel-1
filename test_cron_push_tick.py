"""
test_cron_push_tick.py — POST /cron/push-tick (déclencheur serveur-à-serveur).

Vérifie l'auth constant-time (secret dans le body OU l'en-tête X-Cron-Secret)
et le format de réponse. La logique métier (idempotence, DST, catégories dues)
est couverte par test_push_scheduler.py.
"""

import sys
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

import os
for _k, _v in {
    "SECRET_KEY": "t", "VERIFY_TOKEN": "t", "ADMIN_PASSWORD": "t",
    "DATABASE_URL": "postgresql://t:t@localhost/t",
    "STRIPE_SK": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "WHATSAPP_TOKEN": "t", "PHONE_NUMBER_ID": "t", "GROQ_API_KEY": "t",
    "RESEND_API_KEY": "t", "META_APP_SECRET": "t", "DAILY_SECRET": "cron-secret-xyz",
    "SEO_SECRET": "t",
}.items():
    os.environ.setdefault(_k, _v)

import auryel_bot as A
import push_scheduler as PS

_STATE = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _STATE["pass"] += 1
        print(f"OK  {label}")
    else:
        _STATE["fail"] += 1
        print(f"XX  {label}")


A.app.config["TESTING"] = True
try:
    A.limiter.enabled = False
except Exception:
    pass
client = A.app.test_client()

# push_tick neutralisé : on ne teste ici que l'auth + le contrat de réponse.
A_push_tick_calls = []


def _fake_push_tick(now_utc, store, sender, schedule=None):
    A_push_tick_calls.append(now_utc)
    return {"ticked_at": now_utc.isoformat(), "due": [], "sent": 0,
            "skipped_no_device": 0, "failed": 0, "deduped": 0}


PS.push_tick = _fake_push_tick

_rules = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
check(("/cron/push-tick", "POST") in _rules, "0 route enregistrée")

check(client.post("/cron/push-tick", json={}).status_code == 401,
      "1 aucun secret -> 401")
check(client.post("/cron/push-tick", json={"secret": "nope"}).status_code == 401,
      "2 mauvais secret -> 401")

r = client.post("/cron/push-tick", json={"secret": "cron-secret-xyz"})
check(r.status_code == 200 and r.get_json().get("status") == "ok",
      "3 bon secret (body) -> 200 {status: ok}")
check(len(A_push_tick_calls) == 1, "3b push_tick appelé une fois")

r = client.post("/cron/push-tick", headers={"X-Cron-Secret": "cron-secret-xyz"})
check(r.status_code == 200, "4 bon secret (en-tête X-Cron-Secret) -> 200")

r = client.post("/cron/push-tick", json={"secret": "cron-secret-xyz"})
body = r.get_json()
check(all(k in body for k in ("due", "sent", "skipped_no_device", "failed",
                              "deduped")),
      "5 réponse porte le résumé (sans jeton ni donnée perso)")

print(f"\n{_STATE['pass']} OK / {_STATE['fail']} XX")
raise SystemExit(1 if _STATE["fail"] else 0)
