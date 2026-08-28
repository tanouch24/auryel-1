"""
test_consultation_engine.py — B4.1 : schéma additif + moteur atomique de
consultations 2 h / crédits (open_or_get_consultation, get_consultation_state,
provision_allowance, grant_earned_credit).

100 % local : psycopg2 mocké, get_conn -> fausse DB en mémoire dotée de VRAIS
verrous de ligne (threading.Lock) pour modéliser SELECT ... FOR UPDATE et prouver
la sérialisation de deux "premiers messages" concurrents. Aucune vraie DB, aucun
réseau, aucun appel LLM. Style aligné sur test_auth.py / test_app_chat.py
(script, pas pytest).
"""

import sys
import threading
from unittest.mock import MagicMock

sys.modules["psycopg2"] = MagicMock()

import os
for _k, _v in {
    "SECRET_KEY": "test-secret-key",
    "VERIFY_TOKEN": "test",
    "ADMIN_PASSWORD": "test",
    "CRON_SECRET": "test",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "STRIPE_SK": "sk_test_placeholder",
    "STRIPE_WEBHOOK_SECRET": "whsec_placeholder",
    "WHATSAPP_TOKEN": "test",
    "PHONE_NUMBER_ID": "test",
    "GROQ_API_KEY": "test",
    "RESEND_API_KEY": "test",
    "META_APP_SECRET": "test",
    "DAILY_SECRET": "test",
    "SEO_SECRET": "test",
    "TAROT_MEDIA_UPLOAD_DISABLED": "1",
}.items():
    os.environ.setdefault(_k, _v)

from datetime import timedelta

import auryel_bot as A

_STATE = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _STATE["pass"] += 1
        print(f"✅ {label}")
    else:
        _STATE["fail"] += 1
        print(f"❌ {label}")


# ---------------------------------------------------------------------------
# Fausse base de données en mémoire + verrous de ligne pour FOR UPDATE
# ---------------------------------------------------------------------------
DB = {"accounts": [], "consultations": [], "consultation_allowance": [], "earned_credits": []}
_STORE_LOCK = threading.RLock()
_ROW_LOCKS = {}
_ROW_LOCKS_GUARD = threading.Lock()


def _row_lock(key):
    with _ROW_LOCKS_GUARD:
        lk = _ROW_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _ROW_LOCKS[key] = lk
        return lk


def reset_db():
    with _STORE_LOCK:
        for k in DB:
            DB[k].clear()
    with _ROW_LOCKS_GUARD:
        _ROW_LOCKS.clear()


def seed_account(user_id, deleted_at=None, email="u@example.com"):
    with _STORE_LOCK:
        DB["accounts"].append({"user_id": str(user_id), "email": email, "deleted_at": deleted_at})


def _norm(sql):
    return " ".join(sql.split())


def _safe_remove(lst, obj):
    with _STORE_LOCK:
        try:
            lst.remove(obj)
        except ValueError:
            pass


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._result = None
        self._rows = None
        self.rowcount = -1

    def fetchone(self):
        return self._result

    def fetchall(self):
        return self._rows or []

    def close(self):
        pass

    # -- staging (appliqué tout de suite, annulable au rollback) ----------
    def _stage_append(self, table, obj):
        with _STORE_LOCK:
            DB[table].append(obj)
        self._conn._undo.append(lambda: _safe_remove(DB[table], obj))

    def _stage_update(self, row, changes):
        old = {k: row.get(k) for k in changes}
        with _STORE_LOCK:
            row.update(changes)

        def _undo():
            with _STORE_LOCK:
                row.update(old)

        self._conn._undo.append(_undo)

    def _acquire(self, key, blocking=True):
        lk = _row_lock(key)
        got = lk.acquire(blocking)
        if got:
            self._conn._locks.append(lk)
        return got

    # -- execute -------------------------------------------------------
    def execute(self, sql, params=()):
        k = _norm(sql)
        p = params or ()
        self.rowcount = -1
        self._result = None
        self._rows = None

        if k == ("SELECT user_id FROM accounts WHERE user_id=%s AND deleted_at IS NULL "
                 "FOR UPDATE"):
            (uid,) = p
            uid = str(uid)
            self._acquire("accounts:" + uid, blocking=True)
            with _STORE_LOCK:
                r = next((a for a in DB["accounts"]
                          if a["user_id"] == uid and a["deleted_at"] is None), None)
            self._result = (uid,) if r else None

        elif k == ("SELECT id, advisor_id, started_at, expires_at, credit_source "
                   "FROM consultations WHERE user_id=%s AND expires_at > %s "
                   "ORDER BY started_at DESC LIMIT 1"):
            uid, now = p
            uid = str(uid)
            with _STORE_LOCK:
                rows = [c for c in DB["consultations"]
                        if c["user_id"] == uid and c["expires_at"] > now]
            rows.sort(key=lambda c: c["started_at"], reverse=True)
            if rows:
                c0 = rows[0]
                self._result = (c0["id"], c0["advisor_id"], c0["started_at"],
                                c0["expires_at"], c0["credit_source"])

        elif k == ("SELECT period_start, monthly_limit, monthly_used "
                   "FROM consultation_allowance WHERE user_id=%s AND period_start <= %s "
                   "AND period_end > %s ORDER BY period_start DESC LIMIT 1 FOR UPDATE"):
            uid, now, _now2 = p
            uid = str(uid)
            with _STORE_LOCK:
                rows = [a for a in DB["consultation_allowance"]
                        if a["user_id"] == uid and a["period_start"] <= now and a["period_end"] > now]
            rows.sort(key=lambda a: a["period_start"], reverse=True)
            if rows:
                a0 = rows[0]
                self._acquire("allow:%s:%s" % (uid, a0["period_start"].isoformat()), blocking=True)
                self._result = (a0["period_start"], a0["monthly_limit"], a0["monthly_used"])

        elif k == ("UPDATE consultation_allowance SET monthly_used = monthly_used + 1 "
                   "WHERE user_id=%s AND period_start=%s"):
            uid, ps = p
            uid = str(uid)
            with _STORE_LOCK:
                r = next((a for a in DB["consultation_allowance"]
                          if a["user_id"] == uid and a["period_start"] == ps), None)
            if r is not None:
                self._stage_update(r, {"monthly_used": r["monthly_used"] + 1})
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k == ("SELECT id, source FROM earned_credits WHERE user_id=%s AND consumed_at IS NULL "
                   "ORDER BY granted_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"):
            (uid,) = p
            uid = str(uid)
            with _STORE_LOCK:
                rows = [e for e in DB["earned_credits"]
                        if e["user_id"] == uid and e["consumed_at"] is None]
            rows.sort(key=lambda e: e["granted_at"])
            for e in rows:
                if self._acquire("earned:" + e["id"], blocking=False):
                    self._result = (e["id"], e["source"])
                    break

        elif k == ("INSERT INTO consultations (id, user_id, advisor_id, started_at, expires_at, "
                   "credit_source, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"):
            cid, uid, advisor, started, expires, src, created = p
            self._stage_append("consultations", {
                "id": str(cid), "user_id": str(uid), "advisor_id": advisor,
                "started_at": started, "expires_at": expires,
                "credit_source": src, "created_at": created,
            })
            self.rowcount = 1

        elif k == "UPDATE earned_credits SET consumed_at=%s, consultation_id=%s WHERE id=%s":
            consumed, cons_id, eid = p
            with _STORE_LOCK:
                r = next((e for e in DB["earned_credits"] if e["id"] == str(eid)), None)
            if r is not None:
                self._stage_update(r, {"consumed_at": consumed, "consultation_id": str(cons_id)})
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k == ("SELECT period_start, period_end, monthly_limit, monthly_used "
                   "FROM consultation_allowance WHERE user_id=%s AND period_start <= %s "
                   "AND period_end > %s ORDER BY period_start DESC LIMIT 1"):
            uid, now, _now2 = p
            uid = str(uid)
            with _STORE_LOCK:
                rows = [a for a in DB["consultation_allowance"]
                        if a["user_id"] == uid and a["period_start"] <= now and a["period_end"] > now]
            rows.sort(key=lambda a: a["period_start"], reverse=True)
            if rows:
                a0 = rows[0]
                self._result = (a0["period_start"], a0["period_end"],
                                a0["monthly_limit"], a0["monthly_used"])

        elif k == "SELECT COUNT(*) FROM earned_credits WHERE user_id=%s AND consumed_at IS NULL":
            (uid,) = p
            uid = str(uid)
            with _STORE_LOCK:
                n = sum(1 for e in DB["earned_credits"]
                        if e["user_id"] == uid and e["consumed_at"] is None)
            self._result = (n,)

        elif k == ("INSERT INTO consultation_allowance (user_id, period_start, period_end, "
                   "monthly_limit, monthly_used, created_at) VALUES (%s, %s, %s, %s, 0, %s) "
                   "ON CONFLICT (user_id, period_start) DO NOTHING"):
            uid, ps, pe, lim, created = p
            uid = str(uid)
            with _STORE_LOCK:
                exists = any(a for a in DB["consultation_allowance"]
                             if a["user_id"] == uid and a["period_start"] == ps)
            if exists:
                self.rowcount = 0
            else:
                self._stage_append("consultation_allowance", {
                    "user_id": uid, "period_start": ps, "period_end": pe,
                    "monthly_limit": lim, "monthly_used": 0, "created_at": created,
                })
                self.rowcount = 1

        elif k == ("INSERT INTO earned_credits (id, user_id, source, granted_at, consumed_at, "
                   "consultation_id, metadata) VALUES (%s, %s, %s, %s, NULL, NULL, %s)"):
            eid, uid, src, granted, meta = p
            self._stage_append("earned_credits", {
                "id": str(eid), "user_id": str(uid), "source": src, "granted_at": granted,
                "consumed_at": None, "consultation_id": None, "metadata": meta,
            })
            self.rowcount = 1

        # ---- migration v26 : quota Premium 4 -> 10 (historique) ----
        elif k == "ALTER TABLE consultation_allowance ALTER COLUMN monthly_limit SET DEFAULT 10":
            # Le DEFAULT ne concerne que les futurs INSERT omettant la colonne :
            # aucun effet sur les lignes existantes du fake -> no-op ici.
            self.rowcount = -1

        elif k == "UPDATE consultation_allowance SET monthly_limit = 10 WHERE monthly_limit = 4":
            with _STORE_LOCK:
                n = 0
                for a in DB["consultation_allowance"]:
                    if a["monthly_limit"] == 4:
                        a["monthly_limit"] = 10
                        n += 1
            self.rowcount = n

        # ---- migration v29 : retour du quota Premium standard 10 -> 4 ----
        elif k == "ALTER TABLE consultation_allowance ALTER COLUMN monthly_limit SET DEFAULT 4":
            # DEFAULT -> futurs INSERT omettant la colonne uniquement : no-op ici.
            self.rowcount = -1

        elif k == "UPDATE consultation_allowance SET monthly_limit = 4 WHERE monthly_limit = 10":
            with _STORE_LOCK:
                n = 0
                for a in DB["consultation_allowance"]:
                    if a["monthly_limit"] == 10:
                        a["monthly_limit"] = 4     # monthly_used JAMAIS touché
                        n += 1
            self.rowcount = n

        else:
            raise AssertionError("SQL non géré par le fake B4.1 : " + k)


class FakeConn:
    def __init__(self):
        self._undo = []
        self._locks = []

    def cursor(self):
        return FakeCursor(self)

    def _release_locks(self):
        for lk in self._locks:
            try:
                lk.release()
            except RuntimeError:
                pass
        self._locks = []

    def commit(self):
        self._undo = []
        self._release_locks()

    def rollback(self):
        for fn in reversed(self._undo):
            fn()
        self._undo = []
        self._release_locks()

    def close(self):
        # psycopg2 : close() sur une transaction non commitée = rollback implicite
        if self._undo or self._locks:
            self.rollback()


A.get_conn = lambda: FakeConn()

UID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
UID2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _period(now, days_before=1, days_after=29):
    return (now - timedelta(days=days_before), now + timedelta(days=days_after))


print("=" * 64)
print("TEST CONSULTATION ENGINE — B4.1")
print("=" * 64)

now0 = A._utcnow()

# --- 1. ouverture avec allowance active : used 0 -> 1 --------------------
reset_db(); seed_account(UID)
ps, pe = _period(now0)
A.provision_allowance(UID, ps, pe, monthly_limit=4)
r1 = A.open_or_get_consultation(UID, "selena", now=now0)
check(r1["status"] == "opened" and r1["opened_now"] is True, "1a premier message -> consultation ouverte")
check(r1["consultation"]["credit_source"] == "monthly", "1b credit_source = monthly")
check(r1["consultation"]["advisor_id"] == "selena", "1c advisor_id enregistré")
_st = A.get_consultation_state(UID, now=now0)
check(_st["quota"]["monthly_used"] == 1, "1d monthly_used 0 -> 1")
check(_st["quota"]["is_premium"] is True, "1e is_premium = True (allowance active)")
check(len(DB["consultations"]) == 1, "1f exactement une consultation")

# --- 2. deuxième appel pendant 2 h : même consultation, used reste 1 -----
r2 = A.open_or_get_consultation(UID, "selena", now=now0 + timedelta(minutes=30))
check(r2["status"] == "active" and r2["opened_now"] is False, "2a 2e message < 2 h -> active")
check(r2["consultation"]["id"] == r1["consultation"]["id"], "2b même consultation.id")
_st = A.get_consultation_state(UID, now=now0 + timedelta(minutes=30))
check(_st["quota"]["monthly_used"] == 1, "2c monthly_used reste 1")
check(len(DB["consultations"]) == 1, "2d toujours une seule consultation")

# --- 3. après expiration : nouvelle consultation -----------------------
r3 = A.open_or_get_consultation(UID, "selena", now=now0 + timedelta(hours=3))
check(r3["status"] == "opened" and r3["consultation"]["id"] != r1["consultation"]["id"],
      "3a après 2 h -> nouvelle consultation")
_st = A.get_consultation_state(UID, now=now0 + timedelta(hours=3))
check(_st["quota"]["monthly_used"] == 2, "3b monthly_used 1 -> 2")

# --- 4. 4 utilisations : quota épuisé -------------------------------
reset_db(); seed_account(UID)
ps, pe = _period(now0)
A.provision_allowance(UID, ps, pe, monthly_limit=4)
ids = []
for i in range(4):
    ri = A.open_or_get_consultation(UID, "selena", now=now0 + timedelta(hours=3 * i))
    ids.append(ri["consultation"]["id"])
    check(ri["consultation"]["credit_source"] == "monthly", f"4.{i + 1} ouverture {i + 1}/4 via quota")
_st = A.get_consultation_state(UID, now=now0 + timedelta(hours=13))
check(_st["quota"]["monthly_used"] == 4 and _st["quota"]["monthly_remaining"] == 0,
      "4b quota épuisé : used=4 remaining=0")
check(len(set(ids)) == 4, "4c 4 consultations distinctes")

# --- 5. 5e : no_credit --------------------------------------------
r5 = A.open_or_get_consultation(UID, "selena", now=now0 + timedelta(hours=13))
check(r5["status"] == "no_credit" and "consultation" not in r5, "5a 5e ouverture -> no_credit")
check(len(DB["consultations"]) == 4, "5b aucune consultation créée")
_st = A.get_consultation_state(UID, now=now0 + timedelta(hours=13))
check(_st["quota"]["monthly_used"] == 4, "5c monthly_used inchangé")

# --- 6. 5e avec earned credit : utilise earned --------------------
eid = A.grant_earned_credit(UID, "referral", metadata={"filleul": "x"})
r6 = A.open_or_get_consultation(UID, "selena", now=now0 + timedelta(hours=13))
check(r6["status"] == "opened" and r6["consultation"]["credit_source"] == "referral",
      "6a 5e avec earned -> ouverture via referral")
_ec = next(e for e in DB["earned_credits"] if e["id"] == eid)
check(_ec["consumed_at"] is not None and _ec["consultation_id"] == r6["consultation"]["id"],
      "6b crédit earned marqué consommé + consultation_id APRÈS création de la consultation")
_st = A.get_consultation_state(UID, now=now0 + timedelta(hours=13))
check(_st["quota"]["monthly_used"] == 4, "6c monthly_used toujours 4 (earned != quota)")
check(_st["quota"]["earned_available"] == 0, "6d earned_available 1 -> 0")

# --- 7. utilisateur sans allowance + earned : fonctionne ---------
reset_db(); seed_account(UID2)
A.grant_earned_credit(UID2, "share_30d")
r7 = A.open_or_get_consultation(UID2, "maia", now=now0)
check(r7["status"] == "opened" and r7["consultation"]["credit_source"] == "share_30d",
      "7a sans allowance mais earned -> ouverture via share_30d")
_st = A.get_consultation_state(UID2, now=now0)
check(_st["quota"]["is_premium"] is False, "7b is_premium = False sans allowance active")

# --- 8. utilisateur sans allowance + sans earned : no_credit -----
reset_db(); seed_account(UID2)
r8 = A.open_or_get_consultation(UID2, "maia", now=now0)
check(r8["status"] == "no_credit", "8a sans allowance ni earned -> no_credit")
check(len(DB["consultations"]) == 0, "8b rien créé")

# --- 9. nouvelle période d'abonnement : nouvelle allowance ------
reset_db(); seed_account(UID)
p1s, p1e = now0 - timedelta(days=20), now0 + timedelta(days=10)
A.provision_allowance(UID, p1s, p1e, monthly_limit=4)
A.open_or_get_consultation(UID, "selena", now=now0)                       # P1 used -> 1
p2s, p2e = now0 + timedelta(days=10), now0 + timedelta(days=40)
inserted = A.provision_allowance(UID, p2s, p2e, monthly_limit=4)
check(inserted is True, "9a nouvelle période -> allowance insérée")
r9 = A.open_or_get_consultation(UID, "selena", now=now0 + timedelta(days=15))
check(r9["status"] == "opened" and r9["consultation"]["credit_source"] == "monthly",
      "9b ouverture dans P2 via le nouveau quota")
_p1 = next(a for a in DB["consultation_allowance"] if a["period_start"] == p1s)
_p2 = next(a for a in DB["consultation_allowance"] if a["period_start"] == p2s)
check(_p1["monthly_used"] == 1 and _p2["monthly_used"] == 1, "9c P1 inchangée (1), P2 débitée (1)")

# --- 10. consultation active survit au dépassement de period_end -
reset_db(); seed_account(UID)
psh, peh = now0 - timedelta(days=1), now0 + timedelta(hours=1)           # period_end proche
A.provision_allowance(UID, psh, peh, monthly_limit=4)
r10 = A.open_or_get_consultation(UID, "selena", now=now0)                # expires now0 + 2 h
_later = now0 + timedelta(minutes=90)                                    # > period_end, < expires_at
r10b = A.open_or_get_consultation(UID, "selena", now=_later)
check(r10b["status"] == "active" and r10b["consultation"]["id"] == r10["consultation"]["id"],
      "10a session active continue après period_end")
_st = A.get_consultation_state(UID, now=_later)
check(_st["consultation"] is not None and _st["quota"]["is_premium"] is False,
      "10b consultation encore lue alors que l'allowance n'est plus active")
check(_st["consultation"]["seconds_remaining"] == 30 * 60, "10c secondes restantes = 30 min")

# --- 11. advisor_id de la consultation reste identique ----------
reset_db(); seed_account(UID)
ps, pe = _period(now0)
A.provision_allowance(UID, ps, pe, monthly_limit=4)
r11 = A.open_or_get_consultation(UID, "selena", now=now0)
r11b = A.open_or_get_consultation(UID, "maia", now=now0 + timedelta(minutes=10))
check(r11b["consultation"]["advisor_id"] == "selena",
      "11a advisor figé malgré advisor_id='maia' au 2e appel")
_st = A.get_consultation_state(UID, now=now0 + timedelta(minutes=10))
check(_st["consultation"]["advisor_id"] == "selena",
      "11b get_consultation_state renvoie l'advisor figé")

# --- 12. concurrence : un seul débit / une seule session -------
reset_db(); seed_account(UID)
ps, pe = _period(now0)
A.provision_allowance(UID, ps, pe, monthly_limit=4)
_results = []
_res_lock = threading.Lock()


def _worker():
    rr = A.open_or_get_consultation(UID, "selena", now=now0)
    with _res_lock:
        _results.append(rr)


_threads = [threading.Thread(target=_worker) for _ in range(2)]
for _t in _threads:
    _t.start()
for _t in _threads:
    _t.join()
_statuses = sorted(r["status"] for r in _results)
_ids12 = {r["consultation"]["id"] for r in _results if "consultation" in r}
_alw = next(a for a in DB["consultation_allowance"] if a["user_id"] == UID)
check(len(DB["consultations"]) == 1,
      "12a une seule consultation créée malgré 2 premiers messages simultanés")
check(_alw["monthly_used"] == 1, "12b un seul débit du quota")
check(_statuses == ["active", "opened"], "12c un thread ouvre, l'autre récupère la session active")
check(len(_ids12) == 1, "12d les deux threads voient la même consultation")

# --- 13. compte inconnu -> unknown_account, rien écrit --------
reset_db()
r13 = A.open_or_get_consultation("cccccccc-cccc-4ccc-8ccc-cccccccccccc", "selena", now=now0)
check(r13["status"] == "unknown_account", "13a compte absent -> unknown_account")
check(len(DB["consultations"]) == 0, "13b rien créé")

# --- 14. provision_allowance idempotent ----------------------
reset_db(); seed_account(UID)
ps, pe = _period(now0)
check(A.provision_allowance(UID, ps, pe) is True, "14a 1re provision -> insérée")
check(A.provision_allowance(UID, ps, pe) is False, "14b 2e provision même période -> no-op")
check(len(DB["consultation_allowance"]) == 1, "14c une seule ligne d'allowance")

# --- 15. helpers exposés ------------------------------------
for _name in ("open_or_get_consultation", "get_consultation_state",
              "provision_allowance", "grant_earned_credit", "_utcnow"):
    check(callable(getattr(A, _name, None)), f"15 {_name} exposé")

# --- 16. quota Premium standard = 4 (défaut provision_allowance) -------------
reset_db(); seed_account(UID)
ps, pe = _period(now0)
A.provision_allowance(UID, ps, pe)  # SANS monthly_limit explicite
_st = A.get_consultation_state(UID, now=now0)
check(_st["quota"]["monthly_limit"] == 4 and _st["quota"]["monthly_remaining"] == 4
      and _st["quota"]["is_premium"] is True,
      "16a provision_allowance() sans kwarg -> monthly_limit 4, remaining 4")

# 1 consultation -> remaining 3
A.open_or_get_consultation(UID, "selena", now=now0)
_st = A.get_consultation_state(UID, now=now0)
check(_st["quota"]["monthly_used"] == 1 and _st["quota"]["monthly_remaining"] == 3,
      "16b après 1 consultation mensuelle -> used 1 / remaining 3")

# 4 ouvertures -> used 4, remaining 0 (quota épuisé)
for i in range(1, 4):
    ri = A.open_or_get_consultation(UID, "selena", now=now0 + timedelta(hours=3 * i))
    check(ri["consultation"]["credit_source"] == "monthly", f"16c ouverture {i + 1}/4 via quota")
_st = A.get_consultation_state(UID, now=now0 + timedelta(hours=13))
check(_st["quota"]["monthly_used"] == 4 and _st["quota"]["monthly_remaining"] == 0,
      "16d 4 utilisées -> used 4 / remaining 0 (quota épuisé à 4)")

# 5e tentative sans earned credit -> no_credit
r16 = A.open_or_get_consultation(UID, "selena", now=now0 + timedelta(hours=13))
check(r16["status"] == "no_credit" and "consultation" not in r16,
      "16e 5e ouverture sans earned credit -> no_credit")

# earned credit reste utilisable après épuisement mensuel
A.grant_earned_credit(UID, "referral")
r16e = A.open_or_get_consultation(UID, "selena", now=now0 + timedelta(hours=13))
check(r16e["status"] == "opened" and r16e["consultation"]["credit_source"] == "referral",
      "16f earned credit encore utilisable après quota mensuel épuisé (used reste 4)")
_st = A.get_consultation_state(UID, now=now0 + timedelta(hours=13))
check(_st["quota"]["monthly_used"] == 4, "16g monthly_used reste 4 (earned séparé du quota)")

# --- 17. migration v29 : lignes standard 10 -> 4, monthly_used JAMAIS touché --
reset_db()
UID_A = "aaaaaaaa-1111-4aaa-8aaa-aaaaaaaaaaaa"
UID_B = "bbbbbbbb-2222-4bbb-8bbb-bbbbbbbbbbbb"
UID_C = "cccccccc-3333-4ccc-8ccc-cccccccccccc"
UID_D = "dddddddd-4444-4ddd-8ddd-dddddddddddd"
seed_account(UID_D)
with _STORE_LOCK:
    # standard 10, used 0 / 2 / 4  -> doivent devenir limit 4, used inchangé
    DB["consultation_allowance"].append({
        "user_id": UID_A, "period_start": ps, "period_end": pe,
        "monthly_limit": 10, "monthly_used": 0, "created_at": now0,
    })
    DB["consultation_allowance"].append({
        "user_id": UID_B, "period_start": ps, "period_end": pe,
        "monthly_limit": 10, "monthly_used": 2, "created_at": now0,
    })
    DB["consultation_allowance"].append({
        "user_id": UID_C, "period_start": ps, "period_end": pe,
        "monthly_limit": 10, "monthly_used": 4, "created_at": now0,
    })
    # cas important : used > 4 AU MOMENT de la migration -> used PAS modifié
    DB["consultation_allowance"].append({
        "user_id": UID_D, "period_start": ps, "period_end": pe,
        "monthly_limit": 10, "monthly_used": 7, "created_at": now0,
    })
    # ligne à quota custom (!= 10) -> intacte
    DB["consultation_allowance"].append({
        "user_id": "eeeeeeee-5555-4eee-8eee-eeeeeeeeeeee",
        "period_start": ps, "period_end": pe,
        "monthly_limit": 15, "monthly_used": 1, "created_at": now0,
    })
DB["earned_credits"].append({
    "id": "ec-keep", "user_id": UID_A, "source": "referral",
    "granted_at": now0, "consumed_at": None, "consultation_id": None, "metadata": None,
})
DB["consultations"].append({
    "id": "cons-keep", "user_id": UID_A, "advisor_id": "selena",
    "started_at": now0, "expires_at": now0 + timedelta(hours=2),
    "credit_source": "monthly", "created_at": now0,
})

_conn = A.get_conn()
_c = _conn.cursor()
_c.execute("ALTER TABLE consultation_allowance ALTER COLUMN monthly_limit SET DEFAULT 4")
_c.execute("UPDATE consultation_allowance SET monthly_limit = 4 WHERE monthly_limit = 10")
_conn.commit()
_conn.close()
# rejouable : 2e passage ne touche plus rien
_conn2 = A.get_conn()
_c2 = _conn2.cursor()
_c2.execute("UPDATE consultation_allowance SET monthly_limit = 4 WHERE monthly_limit = 10")
check(_c2.rowcount == 0, "17a v29 idempotent : 2e passage -> 0 ligne")
_conn2.commit(); _conn2.close()

_by = {a["user_id"]: a for a in DB["consultation_allowance"]}
check(_by[UID_A]["monthly_limit"] == 4 and _by[UID_A]["monthly_used"] == 0,
      "17b v29 : 10/used=0 -> 4/used=0 (monthly_used inchangé)")
check(_by[UID_B]["monthly_limit"] == 4 and _by[UID_B]["monthly_used"] == 2,
      "17c v29 : 10/used=2 -> 4/used=2 (monthly_used inchangé)")
check(_by[UID_C]["monthly_limit"] == 4 and _by[UID_C]["monthly_used"] == 4,
      "17d v29 : 10/used=4 -> 4/used=4 (monthly_used inchangé)")
check(_by[UID_D]["monthly_limit"] == 4 and _by[UID_D]["monthly_used"] == 7,
      "17e v29 : 10/used=7 -> 4/used=7 (monthly_used > 4 STRICTEMENT inchangé)")
_st_d = A.get_consultation_state(UID_D, now=now0)
check(_st_d["quota"]["monthly_limit"] == 4 and _st_d["quota"]["monthly_used"] == 7
      and _st_d["quota"]["monthly_remaining"] == 0,
      "17f moteur : used=7 > limit=4 -> monthly_remaining = max(0, 4-7) = 0")
r17d = A.open_or_get_consultation(UID_D, "selena", now=now0)
check(r17d["status"] == "no_credit",
      "17g moteur : allowance saturée (used>=limit) -> no_credit, rien débité")
check(_by["eeeeeeee-5555-4eee-8eee-eeeeeeeeeeee"]["monthly_limit"] == 15
      and _by["eeeeeeee-5555-4eee-8eee-eeeeeeeeeeee"]["monthly_used"] == 1,
      "17h v29 : ligne custom 15 reste 15 (WHERE monthly_limit = 10 seulement)")
check(len(DB["earned_credits"]) == 1 and DB["earned_credits"][0]["consumed_at"] is None,
      "17i v29 : earned_credits inchangés")
check(len(DB["consultations"]) == 1 and DB["consultations"][0]["id"] == "cons-keep",
      "17j v29 : consultations inchangées (aucune période supprimée)")

# --- 18. quota 4 : 4 utilisées -> refus ; earned credit encore utilisable --
reset_db(); seed_account(UID)
ps, pe = _period(now0)
A.provision_allowance(UID, ps, pe)  # -> monthly_limit 4
_ids = set()
for i in range(4):
    ri = A.open_or_get_consultation(UID, "selena", now=now0 + timedelta(hours=3 * i))
    check(ri["status"] == "opened" and ri["consultation"]["credit_source"] == "monthly",
          f"18a ouverture {i + 1}/4 via quota mensuel")
    _ids.add(ri["consultation"]["id"])
check(len(_ids) == 4, "18b 4 consultations distinctes")
_st = A.get_consultation_state(UID, now=now0 + timedelta(hours=13))
check(_st["quota"]["monthly_used"] == 4 and _st["quota"]["monthly_remaining"] == 0,
      "18c quota épuisé : used 4 / remaining 0")

# 5e ouverture mensuelle refusée
r5 = A.open_or_get_consultation(UID, "selena", now=now0 + timedelta(hours=13))
check(r5["status"] == "no_credit" and "consultation" not in r5,
      "18d 5e ouverture -> no_credit (aucune consultation créée)")
check(len(DB["consultations"]) == 4, "18e toujours 4 consultations")

# earned credit encore utilisable à monthly_used=4
_eid = A.grant_earned_credit(UID, "referral")
r_earned = A.open_or_get_consultation(UID, "selena", now=now0 + timedelta(hours=13))
check(r_earned["status"] == "opened" and r_earned["consultation"]["credit_source"] == "referral",
      "18f earned credit encore utilisable après quota mensuel épuisé")
_ec = next(e for e in DB["earned_credits"] if e["id"] == _eid)
check(_ec["consumed_at"] is not None
      and _ec["consultation_id"] == r_earned["consultation"]["id"],
      "18g earned credit marqué consommé + rattaché à la consultation")
_st = A.get_consultation_state(UID, now=now0 + timedelta(hours=13))
check(_st["quota"]["monthly_used"] == 4, "18h monthly_used reste 4 (earned séparé du quota)")

print("-" * 64)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
