import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("psycopg2", MagicMock())
for _key, _value in {
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "SECRET_KEY": "test", "ADMIN_PASSWORD": "test", "GROQ_API_KEY": "test", "RESEND_API_KEY": "test",
    "STRIPE_SK": "test", "STRIPE_WEBHOOK_SECRET": "test",
}.items():
    os.environ.setdefault(_key, _value)

from content_recommendations import (
    ebook_recommendation_allowed,
    follow_up_copy,
    parse_llm_contract,
    resolve_explicit_catalog_recommendation,
)
from push_scheduler import DbPushTickStore, push_tick


def _contract(raw):
    return parse_llm_contract(raw)


def test_structured_contract_accepts_one_candidate_and_keeps_reply_natural():
    reply, recommendation = parse_llm_contract(
        '{"reply":"Je pense à un moment pour ralentir.",'
        '"recommendation":{"type":"meditation","id":"12",'
        '"rationale_code":"sommeil"}}'
    )
    assert reply == "Je pense à un moment pour ralentir."
    assert recommendation == {
        "content_type": "meditation",
        "content_id": "12",
        "rationale_code": "sommeil",
    }


def test_invalid_or_plain_llm_output_never_creates_a_candidate():
    reply, recommendation = parse_llm_contract("Une réponse normale sans contrat.")
    assert reply.startswith("Une réponse normale")
    assert recommendation is None

    reply, recommendation = parse_llm_contract(
        '{"reply":"ok","recommendation":{"type":"ebook", "id":""}}'
    )
    assert reply == "ok"
    assert recommendation is None


def test_ebook_sliding_window_is_per_user_input_and_deterministic():
    now = datetime(2026, 9, 17, tzinfo=timezone.utc)
    assert ebook_recommendation_allowed([], now)
    assert not ebook_recommendation_allowed([now - timedelta(days=29)], now)
    assert ebook_recommendation_allowed([now - timedelta(days=30)], now)


def test_follow_up_wording_never_claims_full_reading():
    assert "commencé" in follow_up_copy("Le sommeil sans combat", True)
    assert "jeter un œil" in follow_up_copy("Le sommeil sans combat", False)
    assert "lu intégralement" not in follow_up_copy("Le sommeil sans combat", True)


def test_llm_contract_explicit_safe_cases():
    assert _contract('{"reply":"Très bien.","recommendation":null}') == (
        "Très bien.", None)
    assert _contract('{"reply":"Voici une piste.","recommendation":{'
                     '"type":"ebook","id":"ebook-real"}}')[1] == {
                         "content_type": "ebook", "content_id": "ebook-real",
                         "rationale_code": None,
                     }
    assert _contract('{"reply":"Je reste avec toi.","recommendation":{'
                     '"type":"ebook","id":"ebook-hallucine"}}')[1]["content_id"] == "ebook-hallucine"
    assert _contract('{"reply":"Je reste avec toi.","recommendation":{'
                     '"type":"ebook","id":"ebook-real","title":"Titre inventé"}}')[1]["content_id"] == "ebook-real"
    assert _contract('{"reply":"Réponse conservée.","recommendation":{'
                     '"type":"unknown","id":"x"}}') == ("Réponse conservée.", None)
    assert _contract('{"reply":"Réponse conservée.","recommendation":{'
                     '"type":"ebook","id":"new"}}')[1] is not None
    assert _contract('{"reply":"Réponse') == ('{"reply":"Réponse', None)
    assert _contract('{"reply":"Catalogue vide."}') == ("Catalogue vide.", None)


def test_plain_reply_with_exact_actionable_meditation_title_gets_safe_candidate():
    candidate = resolve_explicit_catalog_recommendation(
        'Je te recommande la méditation "Relâcher le corps avant la nuit".',
        [{"content_type": "meditation", "content_id": "med-night",
          "title": "Relâcher le corps avant la nuit"}],
    )
    assert candidate == {
        "content_type": "meditation",
        "content_id": "med-night",
        "rationale_code": "explicit_catalog_title",
    }


def test_safe_title_fallback_does_not_create_cards_for_non_actionable_or_fake_text():
    catalog = [{"content_type": "meditation", "content_id": "med-night",
                "title": "Relâcher le corps avant la nuit"}]
    assert resolve_explicit_catalog_recommendation(
        "Je peux te conseiller une méditation si tu veux.", catalog) is None
    assert resolve_explicit_catalog_recommendation(
        "Je peux te conseiller la méditation Relâcher le corps avant la nuit.",
        catalog,
    )["content_id"] == "med-night"
    assert resolve_explicit_catalog_recommendation(
        "Tu m'avais parlé de Relâcher le corps avant la nuit.", catalog) is None
    assert resolve_explicit_catalog_recommendation(
        "Je te recommande Faux contenu inexistant.", catalog) is None
    assert resolve_explicit_catalog_recommendation(
        "Je ne te recommande pas Relâcher le corps avant la nuit.", catalog) is None


@pytest.mark.parametrize("kind, phrase", [
    ("ebook", "Je te recommande « Le sommeil sans combat »."),
    ("exercise", "Je te conseille l'exercice Trois souffles de pause."),
])
def test_safe_title_fallback_supports_ebook_and_exercise(kind, phrase):
    candidate = resolve_explicit_catalog_recommendation(
        phrase,
        [{"content_type": kind, "content_id": f"{kind}-real",
          "title": "Le sommeil sans combat" if kind == "ebook"
          else "Trois souffles de pause"}],
    )
    assert candidate["content_type"] == kind
    assert candidate["content_id"] == f"{kind}-real"


def test_safe_title_fallback_refuses_ambiguous_two_content_titles():
    reply = "Je te recommande Un moment calme."
    catalog = [
        {"content_type": "meditation", "content_id": "med-1",
         "title": "Un moment calme"},
        {"content_type": "exercise", "content_id": "ex-1",
         "title": "Un moment calme"},
    ]
    assert resolve_explicit_catalog_recommendation(reply, catalog) is None


class _RecommendationDb:
    def __init__(self):
        self.accounts = {"account-a", "account-b"}
        self.recommendations = []
        self.lock = threading.RLock()
        self.clock = datetime(2026, 9, 18, tzinfo=timezone.utc)


class _RecommendationConnection:
    def __init__(self, db):
        self.db = db
        self.cursor_obj = _RecommendationCursor(db)

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.cursor_obj.release()

    def rollback(self):
        self.cursor_obj.release()

    def close(self):
        self.cursor_obj.release()


class _RecommendationCursor:
    def __init__(self, db):
        self.db = db
        self.result = None
        self.results = []
        self.locked = False

    def release(self):
        if self.locked:
            self.locked = False
            self.db.lock.release()

    def execute(self, sql, params=()):
        compact = " ".join(sql.split())
        self.result = None
        self.results = []
        if "FROM accounts" in compact and "FOR UPDATE" in compact:
            self.db.lock.acquire()
            self.locked = True
            if params[0] in self.db.accounts:
                self.result = (params[0],)
            return
        if compact.startswith("SELECT id, title_snapshot"):
            uid = params[0]
            if "advisor_id=%s" in compact:
                _, advisor, kind, content_id, cutoff = params
                matches = [r for r in self.db.recommendations
                           if r["user_id"] == uid and r["advisor_id"] == advisor
                           and r["content_type"] == kind and r["content_id"] == content_id
                           and r["created_at"] >= cutoff]
            else:
                _, cutoff = params
                matches = [r for r in self.db.recommendations
                           if r["user_id"] == uid and r["content_type"] == "ebook"
                           and (r["created_at"] > cutoff if "created_at > %s" in compact
                                else r["created_at"] >= cutoff)]
            if matches:
                rec = sorted(matches, key=lambda r: r["created_at"], reverse=True)[0]
                self.result = (rec["id"], rec["title_snapshot"], rec["snapshot"])
            return
        if compact.startswith("INSERT INTO content_recommendations"):
            (rec_id, uid, advisor, kind, content_id, title, rationale, snapshot,
             created_at, displayed_at, assistant_id) = params
            self.db.recommendations.append({
                "id": rec_id, "user_id": uid, "advisor_id": advisor,
                "content_type": kind, "content_id": content_id,
                "title_snapshot": title, "snapshot": snapshot,
                "created_at": created_at, "opened_at": None,
                "download_requested_at": None, "follow_up_sent_at": None,
                "assistant_message_id": assistant_id,
            })
            return
        if "SELECT content_type FROM content_recommendations" in compact:
            rec_id, uid = params
            rec = next((r for r in self.db.recommendations
                        if r["id"] == rec_id and r["user_id"] == uid), None)
            self.result = (rec["content_type"],) if rec else None
            return
        if "UPDATE content_recommendations SET" in compact:
            rec_id, uid = params
            rec = next((r for r in self.db.recommendations
                        if r["id"] == rec_id and r["user_id"] == uid), None)
            if rec:
                field = "opened_at" if "opened_at" in compact else "download_requested_at"
                rec[field] = rec[field] or self.db.clock
            return
        raise AssertionError(f"SQL non couvert par le faux DB: {compact}")

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.results


def _patch_recommendation_db(monkeypatch):
    import auryel_bot as app

    db = _RecommendationDb()
    snapshots = {
        ("ebook", "ebook-1"): {"id": "ebook-1", "title": "Titre canonique", "content_type": "ebook"},
        ("ebook", "ebook-2"): {"id": "ebook-2", "title": "Deuxième ebook", "content_type": "ebook"},
        ("meditation", "med-1"): {"id": "med-1", "title": "Méditation réelle", "content_type": "meditation"},
        ("exercise", "ex-1"): {"id": "ex-1", "title": "Exercice réel", "content_type": "exercise"},
    }
    monkeypatch.setattr(app, "get_conn", lambda: _RecommendationConnection(db))
    monkeypatch.setattr(app, "_utcnow", lambda: db.clock)
    monkeypatch.setattr(app, "_recommendation_snapshot",
                        lambda _cursor, kind, content_id: snapshots.get((kind, content_id)))
    return app, db


@pytest.mark.parametrize("advisor_id", [
    "selena", "luna", "maia", "thea", "cassandre",
    "myriam", "orion", "ezra", "kael", "raphael",
])
def test_all_advisors_use_common_explicit_meditation_engine(monkeypatch, advisor_id):
    app, db = _patch_recommendation_db(monkeypatch)
    candidate = resolve_explicit_catalog_recommendation(
        'Je te recommande la méditation "Méditation réelle".',
        [{"content_type": "meditation", "content_id": "med-1",
          "title": "Méditation réelle"}],
    )
    saved = app.save_content_recommendation("account-a", advisor_id, candidate)
    assert saved["content_type"] == "meditation"
    assert saved["content_id"] == "med-1"
    assert saved["title"] == "Méditation réelle"
    assert db.recommendations[0]["advisor_id"] == advisor_id


def test_persistence_enforces_ebook_window_boundaries_and_account_isolation(monkeypatch):
    app, db = _patch_recommendation_db(monkeypatch)
    first = app.save_content_recommendation("account-a", "luna", {
        "content_type": "ebook", "content_id": "ebook-1", "title": "faux"
    })
    assert first["title"] == "Titre canonique"
    assert app.save_content_recommendation("account-a", "luna", {
        "content_type": "ebook", "content_id": "ebook-2"
    }) is None
    same = app.save_content_recommendation("account-a", "luna", {
        "content_type": "ebook", "content_id": "ebook-1"
    })
    assert same["recommendation_id"] == first["recommendation_id"]

    db.recommendations[0]["created_at"] = db.clock - timedelta(days=29)
    assert app.save_content_recommendation("account-a", "luna", {
        "content_type": "ebook", "content_id": "ebook-2"
    }) is None
    db.recommendations[0]["created_at"] = db.clock - timedelta(days=30)
    assert app.save_content_recommendation("account-a", "luna", {
        "content_type": "ebook", "content_id": "ebook-2"
    }) is not None
    assert app.save_content_recommendation("account-b", "luna", {
        "content_type": "ebook", "content_id": "ebook-1"
    }) is not None


def test_catalog_validation_and_content_deduplication(monkeypatch):
    app, db = _patch_recommendation_db(monkeypatch)
    assert app.save_content_recommendation("account-a", "luna", {
        "content_type": "ebook", "content_id": "missing"
    }) is None
    assert app.save_content_recommendation("account-a", "luna", {
        "content_type": "meditation", "content_id": "med-1"
    }) is not None
    duplicate = app.save_content_recommendation("account-a", "luna", {
        "content_type": "meditation", "content_id": "med-1"
    })
    assert len(db.recommendations) == 1
    assert duplicate["recommendation_id"] == db.recommendations[0]["id"]
    db.recommendations[0]["created_at"] = db.clock - timedelta(days=8)
    assert app.save_content_recommendation("account-a", "luna", {
        "content_type": "meditation", "content_id": "med-1"
    }) is not None
    assert app.save_content_recommendation("account-a", "luna", {
        "content_type": "exercise", "content_id": "ex-1"
    }) is not None


def test_two_concurrent_new_ebooks_serialize_under_account_lock(monkeypatch):
    app, db = _patch_recommendation_db(monkeypatch)
    barrier = threading.Barrier(2)
    results = []

    def create(content_id):
        barrier.wait()
        results.append(app.save_content_recommendation("account-a", "luna", {
            "content_type": "ebook", "content_id": content_id
        }))

    threads = [threading.Thread(target=create, args=(content_id,))
               for content_id in ("ebook-1", "ebook-2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(result is not None for result in results) == 1
    assert len(db.recommendations) == 1


def test_events_are_scoped_idempotent_and_never_mean_read(monkeypatch):
    app, db = _patch_recommendation_db(monkeypatch)
    ebook = app.save_content_recommendation("account-a", "luna", {
        "content_type": "ebook", "content_id": "ebook-1"
    })
    meditation = app.save_content_recommendation("account-a", "luna", {
        "content_type": "meditation", "content_id": "med-1"
    })
    ebook_id = ebook["recommendation_id"]
    med_id = meditation["recommendation_id"]
    assert app.record_content_event("account-a", ebook_id, "opened") == "ok"
    assert app.record_content_event("account-a", ebook_id, "opened") == "ok"
    assert app.record_content_event("account-a", ebook_id, "download_requested") == "ok"
    assert app.record_content_event("account-a", ebook_id, "download_requested") == "ok"
    assert app.record_content_event("account-a", med_id, "download_requested") == "invalid"
    assert app.record_content_event("account-b", ebook_id, "opened") == "not_found"
    assert app.record_content_event("account-a", "00000000-0000-0000-0000-000000000000", "opened") == "not_found"
    row = db.recommendations[0]
    assert "read" not in row and "read_at" not in row
    assert row["opened_at"] is not None and row["download_requested_at"] is not None


class _GuidanceCursor:
    def __init__(self, state):
        self.state = state
        self.rows = []
        self.one = None

    def execute(self, sql, params=()):
        compact = " ".join(sql.split())
        self.rows = []
        self.one = None
        if "SELECT DISTINCT ON (c.user_id)" in compact:
            return
        if "FROM content_recommendations r" in compact:
            rec = self.state.get("recommendation")
            if (rec and self.state.get("eligible") and self.state.get("active")
                    and not self.state.get("followed")):
                self.rows = [(rec["id"], rec["user_id"], rec["advisor_id"],
                              rec["title"], rec["opened_at"], rec["created_at"],
                              rec["assistant_id"])]
            return
        if "FROM messages m" in compact:
            self.one = (1,) if self.state.get("returned") else None
            return
        if "FROM consultations c" in compact:
            self.one = (1,) if self.state.get("active_consultation") else None
            return
        raise AssertionError(f"SQL guidance non couvert: {compact}")

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one


class _GuidanceConnection:
    def __init__(self, state):
        self.cursor_obj = _GuidanceCursor(state)

    def cursor(self):
        return self.cursor_obj

    def close(self):
        pass


def _recommendation_guidance_job(**overrides):
    now = datetime(2026, 9, 18, 10, tzinfo=timezone.utc)
    state = {
        "eligible": True, "active": True, "followed": False,
        "returned": False, "active_consultation": False,
        "recommendation": {
            "id": "rec-guidance", "user_id": "account-a", "advisor_id": "luna",
            "title": "Titre canonique", "opened_at": None,
            "created_at": now - timedelta(days=3), "assistant_id": None,
        },
    }
    recommendation_overrides = overrides.pop("recommendation", {})
    state.update(overrides)
    state["recommendation"].update(recommendation_overrides)
    store = DbPushTickStore(lambda: _GuidanceConnection(state))
    jobs = store.personal_guidance_jobs(now)
    return [job for job in jobs if job.get("recommendation_id")], state


def test_personal_guidance_ebook_j3_wording_dedupe_advisor_and_inactive(monkeypatch):
    jobs, _ = _recommendation_guidance_job()
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Luna"
    assert jobs[0]["data"] == {"advisor": "luna"}
    assert "jeter un œil" in jobs[0]["body"]

    jobs, _ = _recommendation_guidance_job(
        recommendation={"opened_at": datetime(2026, 9, 17, tzinfo=timezone.utc)})
    assert "commencé" in jobs[0]["body"]
    assert _recommendation_guidance_job(eligible=False)[0] == []
    assert _recommendation_guidance_job(followed=True)[0] == []
    assert _recommendation_guidance_job(
        returned=True, recommendation={"assistant_id": 10})[0] == []
    assert _recommendation_guidance_job(active=False)[0] == []


def test_personal_guidance_cap_and_weekly_life_lesson_absence():
    import push_scheduler as scheduler

    class _Schedule:
        def due_categories(self, _now):
            return [("personal_guidance", "p")]

    class _Store:
        def __init__(self):
            self.claims = 0

        def recipients(self):
            return ["account-a"]

        def global_push_allowed(self, _uid, _now):
            return False

        def active_tokens(self, _uid):
            return ["token"]

        def claim(self, *_args):
            self.claims += 1
            return True

    sender = MagicMock(config=MagicMock(dry_run=False, enabled=True))
    sender.send.return_value.outcome = "sent"
    store = _Store()
    summary = push_tick(datetime(2026, 9, 18, tzinfo=timezone.utc), store, sender, _Schedule())
    assert summary["sent"] == 0 and store.claims == 0
    assert "weekly_life_lesson" not in scheduler.ALLOWED_TYPES
