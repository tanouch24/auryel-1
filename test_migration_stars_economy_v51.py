"""
test_migration_stars_economy_v51.py — Prompt technique 1 (économie Étoiles
v2) : migration v51 (plafond quotidien multi-claims + nouveau barème +
nouveaux paliers Consultation Express).

Même méthode que test_migration_reward_stars_v49.py /
test_migration_express_minigames_v50.py : psycopg2 mocké, on prouve la
migration au niveau du SOURCE de init_db().
"""

import sys
import inspect
import re
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

import os
for _k, _v in {
    "SECRET_KEY": "test-secret-key", "VERIFY_TOKEN": "test", "ADMIN_PASSWORD": "test",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "STRIPE_SK": "sk_test_placeholder", "STRIPE_WEBHOOK_SECRET": "whsec_placeholder",
    "WHATSAPP_TOKEN": "test", "PHONE_NUMBER_ID": "test", "GROQ_API_KEY": "test",
    "RESEND_API_KEY": "test", "META_APP_SECRET": "test", "DAILY_SECRET": "test",
    "SEO_SECRET": "test",
}.items():
    os.environ.setdefault(_k, _v)

import auryel_bot as A

_STATE = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _STATE["pass"] += 1
        print(f"OK  {label}")
    else:
        _STATE["fail"] += 1
        print(f"XX  {label}")


SRC = inspect.getsource(A.init_db)

_m51 = re.search(r"#\s*Migration v51\b", SRC)
assert _m51, "bloc 'Migration v51' introuvable dans init_db()"
_m50 = re.search(r"#\s*Migration v50\b", SRC)
assert _m50, "bloc 'Migration v50' introuvable dans init_db()"
assert _m51.start() > _m50.start(), "v51 doit venir APRES v50"

_tail = SRC[_m51.start():]
_close = _tail.find("conn.close()")
assert _close != -1, "conn.close() final introuvable apres le bloc v51"
# Borne au DÉBUT du prochain bloc "Migration vNN" s'il existe (jamais un
# futur v52 qui se retrouverait inclus ici) — même correctif appliqué à
# test_migration_reward_stars_v49.py / test_migration_express_minigames_v50.py.
_next = re.search(r"#\s*Migration v\d+\b", _tail[1:])
_end = (_next.start() + 1) if _next else (_close + len("conn.close()"))
V51 = _tail[:_end]


def _nocomment(block):
    return "\n".join(
        line for line in block.splitlines() if not line.lstrip().startswith("#")
    )


V51_NC = _nocomment(V51)
V51_LOW = V51_NC.lower()
V51_FLAT = re.sub(r"[\s\"']+", " ", V51_LOW)

print("-" * 64)
print("A. Ordre & présence")
check("Migration v50" in SRC and SRC.index("Migration v50") < _m51.start(),
      "1 v51 ajoutée APRÈS v50")

print("-" * 64)
print("B. daily_action_claims — colonne additive + index remplacé")
check("alter table daily_action_claims add column if not exists claim_seq "
      "integer not null default 1" in V51_FLAT,
      "2 ADD COLUMN IF NOT EXISTS claim_seq (jamais destructif, DEFAULT 1 "
      "-> les lignes déjà écrites restent valides)")
check("drop index if exists uq_daily_action_claims_user_action_date" in V51_FLAT,
      "3 ancien index UNIQUE (user_id, action_key, claim_date) supprimé "
      "(sinon il replafonnerait TOUTE règle à 1/jour quel que soit "
      "daily_limit)")
check(
    re.search(
        r"create unique index if not exists "
        r"uq_daily_action_claims_user_action_date_seq "
        r"on daily_action_claims \(user_id, action_key, claim_date, claim_seq\)",
        V51_FLAT,
    ) is not None,
    "4 nouvel index UNIQUE (user_id, action_key, claim_date, claim_seq)",
)
check("alter table accounts" not in V51_FLAT,
      "5 v51 ne touche JAMAIS accounts directement (débit/crédit passent "
      "par les primitives existantes)")
check("drop table" not in V51_FLAT and "truncate" not in V51_FLAT
      and "delete from" not in V51_FLAT,
      "6 aucun DROP TABLE / TRUNCATE / DELETE dans le bloc v51 (DROP INDEX "
      "ne touche qu'une contrainte, jamais des données)")

print("-" * 64)
print("C. Nouveau barème reward_rules — UPDATE GARDÉS par l'ANCIEN montant")
check(
    re.search(
        r"update reward_rules set stars_amount=%s, updated_at=%s "
        r"where rule_key=%s and stars_amount=%s",
        V51_FLAT,
    ) is not None,
    "6b le barème est mis à jour via UNE requête paramétrée (executemany) "
    "GARDÉE par l'ancien montant — jamais un montant en dur par règle",
)
_BAREME = {
    "wake_completed": (5, 10),
    "share_completed": (15, 20),
    "mini_game_completed": (15, 20),
}
for key, (old, new) in _BAREME.items():
    check(
        re.search(rf"\(\s*{new}\s*,\s*\w+\s*,\s*{key}\s*,\s*{old}\s*\)", V51_FLAT)
        is not None,
        f"7.{key} tuple ({new}, …, '{key}', {old}) présent dans la liste "
        f"executemany : {old} -> {new} ⭐, GARDÉ par l'ancien montant "
        f"(un opérateur ayant déjà ajusté {key} n'est jamais écrasé)",
    )

print("-" * 64)
print("D. Activation GARDÉE de rewarded_ad_completed (jamais un écrasement)")
check(
    re.search(
        r"update reward_rules set stars_amount=10, enabled=true, "
        r"daily_limit=5.*?where rule_key= ?rewarded_ad_completed and "
        r"enabled=false",
        V51_FLAT,
    ) is not None,
    "8 UPDATE reward_rules ... WHERE rule_key='rewarded_ad_completed' AND "
    "enabled=FALSE -> stars_amount=10, daily_limit=5",
)

print("-" * 64)
print("E. Consultation Express — nouveaux paliers")
check(
    re.search(
        r"update express_products set stars_cost=400 "
        r"where product_key= ?express_consultation_10min and stars_cost=500",
        V51_FLAT,
    ) is not None,
    "9 express_consultation_10min : 500 -> 400 ⭐ (durée inchangée), "
    "GARDÉ par l'ancien coût",
)
_TIERS = {
    "express_consultation_15min": (500, 900),
    "express_consultation_30min": (1000, 1800),
    "express_consultation_45min": (1500, 2700),
    "express_consultation_60min": (2000, 3600),
}
for key, (cost, seconds) in _TIERS.items():
    check(
        re.search(rf"{key}.*?{cost}.*?{seconds}", V51_FLAT) is not None,
        f"10.{key} seed {cost} ⭐ -> {seconds} s",
    )
check("on conflict (product_key) do nothing" in V51_FLAT,
      "11 seed express_products : ON CONFLICT DO NOTHING (jamais un "
      "remplacement d'un produit déjà personnalisé)")

print("-" * 64)
print("F. Idempotence de la migration elle-même (rejouabilité)")
check(V51_FLAT.count("if not exists") >= 2,
      "12 ADD COLUMN / CREATE INDEX utilisent IF NOT EXISTS")
check("do update" not in V51_FLAT,
      "13 jamais un DO UPDATE (seeds/activations toujours GARDÉS par une "
      "clause WHERE sur l'ancien état, jamais un écrasement inconditionnel)")

print("-" * 64)
print("G. _award_stars_tx compte AVANT d'insérer (support daily_limit > 1)")
AWARD_SRC = inspect.getsource(A._award_stars_tx)
check("select count(*) from daily_action_claims" in AWARD_SRC.lower(),
      "14 _award_stars_tx interroge COUNT(*) daily_action_claims avant "
      "toute décision (jamais un simple INSERT ON CONFLICT comme seul "
      "garde-fou, insuffisant pour daily_limit > 1)")
check("claim_seq" in AWARD_SRC,
      "15 _award_stars_tx insère avec claim_seq (numérotation du jour)")
check(
    re.search(
        r"on conflict \(user_id, action_key, claim_date, claim_seq\)",
        AWARD_SRC.lower(),
    ) is not None,
    "16 INSERT daily_action_claims protégé par le NOUVEL index composite "
    "(filet de sécurité en base, pas le seul rempart)",
)

print("-" * 64)
total = _STATE["pass"] + _STATE["fail"]
print(f"RESULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko (sur {total})")
sys.exit(1 if _STATE["fail"] else 0)
