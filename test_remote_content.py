"""
test_remote_content.py — Partie D : contenu distant (méditations + contenu du
jour) + surface admin + validation des URL média.

100 % local : psycopg2 mocké, fausse DB en mémoire modélisant `meditation_catalog`
et `daily_content`. Aucun réseau, aucun LLM. Couvre :
  - GET /api/app/content/meditations : contrat versionné, actif/publié seulement,
    ETag / If-None-Match -> 304, tri sort_order.
  - GET /api/app/content/today : date serveur Europe/Paris (jamais le client),
    daily_thought / daily_publication séparés, type absent -> null.
  - admin : upsert méditation (version++), toggle, upsert contenu du jour,
    auth admin + CSRF, validation stricte des URL (HTTPS, allowlist).
  - _validate_media_url en unité.
  - migration v43 additive (inspection de source).
"""

import sys
import inspect
from datetime import datetime, timezone, date
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


UID = "11111111-1111-4111-8111-111111111111"

MEDS = []      # dicts
DAILY = []     # dicts
_SEQ = [0]


def _reset():
    MEDS.clear()
    DAILY.clear()
    _SEQ[0] = 0


def _norm(s):
    return " ".join(s.split())


class Cur:
    def __init__(self):
        self._one = None
        self._rows = None
        self.rowcount = -1

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows or []

    def close(self):
        pass

    def execute(self, sql, params=()):
        k = _norm(sql)
        p = tuple(params or ())
        self._one = None
        self._rows = None
        self.rowcount = -1

        if k == _norm(
            "SELECT id, slug, title, description, category, duration_seconds, "
            "audio_url, image_url, sort_order, published_at, updated_at, version "
            "FROM meditation_catalog WHERE is_active = TRUE "
            "AND (published_at IS NULL OR published_at <= NOW()) "
            "ORDER BY sort_order ASC, id ASC"):
            now = datetime.now(timezone.utc)
            rows = [m for m in MEDS if m["is_active"]
                    and (m["published_at"] is None or m["published_at"] <= now)]
            rows.sort(key=lambda m: (m["sort_order"], m["id"]))
            self._rows = [(m["id"], m["slug"], m["title"], m["description"],
                           m["category"], m["duration_seconds"], m["audio_url"],
                           m["image_url"], m["sort_order"], m["published_at"],
                           m["updated_at"], m["version"]) for m in rows]

        elif k == _norm(
            "SELECT content_type, title, text, explanation, image_url "
            "FROM daily_content WHERE publication_date = %s AND is_active = TRUE "
            "AND content_type IN ('daily_thought', 'daily_publication')"):
            (pdate,) = p
            self._rows = [(d["content_type"], d["title"], d["text"],
                           d["explanation"], d["image_url"])
                          for d in DAILY
                          if d["publication_date"] == pdate and d["is_active"]]

        elif k.startswith("INSERT INTO meditation_catalog"):
            (mid, slug, title, desc, cat, dur, audio, image, sort_o, pub) = p
            existing = next((m for m in MEDS if m["slug"] == slug), None)
            if existing:
                existing.update(title=title, description=desc, category=cat,
                                duration_seconds=dur, audio_url=audio,
                                image_url=image, sort_order=sort_o,
                                published_at=pub,
                                updated_at=datetime.now(timezone.utc),
                                version=existing["version"] + 1)
                self._one = (existing["id"], existing["version"])
            else:
                MEDS.append({"id": mid, "slug": slug, "title": title,
                             "description": desc, "category": cat,
                             "duration_seconds": dur, "audio_url": audio,
                             "image_url": image, "sort_order": sort_o,
                             "is_active": True, "published_at": pub,
                             "updated_at": datetime.now(timezone.utc),
                             "version": 1})
                self._one = (mid, 1)

        elif k.startswith("UPDATE meditation_catalog SET is_active=%s"):
            is_active, mid = p
            m = next((x for x in MEDS if x["id"] == mid), None)
            if m:
                m["is_active"] = is_active
                m["version"] += 1
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k.startswith("INSERT INTO daily_content"):
            (did, ctype, pdate, title, text, expl, image) = p
            existing = next((d for d in DAILY
                             if d["content_type"] == ctype
                             and d["publication_date"] == pdate), None)
            if existing:
                existing.update(title=title, text=text, explanation=expl,
                                image_url=image)
                self._one = (existing["id"],)
            else:
                DAILY.append({"id": did, "content_type": ctype,
                              "publication_date": pdate, "title": title,
                              "text": text, "explanation": expl,
                              "image_url": image, "is_active": True})
                self._one = (did,)

        elif k.startswith("UPDATE daily_content SET is_active=%s"):
            is_active, did = p
            d = next((x for x in DAILY if x["id"] == did), None)
            if d:
                d["is_active"] = is_active
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k.startswith("SELECT id, slug, title, category, duration_seconds"):
            self._rows = [(m["id"], m["slug"], m["title"], m["category"],
                           m["duration_seconds"], m["audio_url"], m["image_url"],
                           m["sort_order"], m["is_active"], m["published_at"],
                           m["version"]) for m in MEDS]
        elif k.startswith("SELECT id, content_type, publication_date, title, text"):
            self._rows = [(d["id"], d["content_type"], d["publication_date"],
                           d["title"], d["text"], d["explanation"],
                           d["image_url"], d["is_active"]) for d in DAILY]
        elif k.startswith("SELECT id, slug, title, category, tags, video_url"):
            # section vidéos de /admin/content — hors périmètre de ce test
            # (couverte par test_relaxation_videos.py) : catalogue vide.
            self._rows = []
        elif k.startswith("INSERT INTO admin_logs"):
            self.rowcount = 1
        else:
            raise AssertionError("SQL non géré par le fake content : " + k)


class Conn:
    def cursor(self):
        return Cur()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


A.get_conn = lambda: Conn()
A.app.config["TESTING"] = True
try:
    A.limiter.enabled = False
except Exception:
    pass
client = A.app.test_client()

A.resolve_app_session = lambda tok: (
    {"session_id": "s", "user_id": UID, "email": "u@auryel.co"} if tok else None
)
H = {"Authorization": "Bearer x"}


def _seed_med(slug, sort_order=0, is_active=True, published_at=None,
              audio="https://cdn.auryel.app/a.m4a", image=None, version=1):
    _SEQ[0] += 1
    MEDS.append({"id": f"med-{_SEQ[0]:04d}-4000-8000-000000000000".replace("med-", "0000")[:8]
                 + f"-0000-4000-8000-00000000000{_SEQ[0]}"[:28],
                 "slug": slug, "title": slug.title(), "description": "d",
                 "category": "calme", "duration_seconds": 180, "audio_url": audio,
                 "image_url": image, "sort_order": sort_order,
                 "is_active": is_active, "published_at": published_at,
                 "updated_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
                 "version": version})


# ===========================================================================
# 0. _validate_media_url — unité
# ===========================================================================
u, e = A._validate_media_url("https://cdn.example.com/x.m4a", required=True)
check(u and e is None, "0a URL HTTPS valide acceptée")
check(A._validate_media_url("http://cdn.example.com/x.m4a", required=True)[1] == "url_not_https",
      "0b HTTP rejeté (url_not_https)")
check(A._validate_media_url("ftp://x/y", required=True)[1] == "url_not_https",
      "0c schéma non-HTTPS rejeté")
check(A._validate_media_url("", required=True)[1] == "url_required",
      "0d vide + required -> url_required")
check(A._validate_media_url("", required=False) == (None, None),
      "0e vide + facultatif -> (None, None)")
check(A._validate_media_url("https://x/" + "a" * 3000, required=True)[1] == "url_too_long",
      "0f URL trop longue rejetée")
_saved = A._CONTENT_MEDIA_ALLOWED_HOSTS
A._CONTENT_MEDIA_ALLOWED_HOSTS = ("cdn.auryel.app",)
check(A._validate_media_url("https://evil.com/x", required=True)[1] == "url_host_not_allowed",
      "0g hôte hors allowlist rejeté")
check(A._validate_media_url("https://cdn.auryel.app/x.m4a", required=True)[0]
      == "https://cdn.auryel.app/x.m4a", "0h hôte dans l'allowlist accepté")
A._CONTENT_MEDIA_ALLOWED_HOSTS = _saved

# ===========================================================================
# 1. GET /api/app/content/meditations
# ===========================================================================
_reset()
check(client.get("/api/app/content/meditations").status_code == 401,
      "1a sans Bearer -> 401")

_seed_med("b_zen", sort_order=2)
_seed_med("a_calme", sort_order=1)
_seed_med("inactif", sort_order=0, is_active=False)
_seed_med("futur", sort_order=0, published_at=datetime(2999, 1, 1, tzinfo=timezone.utc))
r = client.get("/api/app/content/meditations", headers=H)
j = r.get_json()
check(r.status_code == 200 and set(j.keys()) == {"version", "catalog_version", "meditations"},
      "1b contrat = version / catalog_version / meditations")
slugs = [m["slug"] for m in j["meditations"]]
check(slugs == ["a_calme", "b_zen"],
      "1c seules les entrées actives+publiées, triées par sort_order")
check(set(j["meditations"][0].keys()) == {
    "id", "slug", "title", "description", "category", "duration_seconds",
    "audio_url", "image_url", "sort_order", "published_at", "version"},
      "1d chaque méditation : contrat de champs complet")
etag = r.headers.get("ETag")
check(etag and etag.strip('"') == j["catalog_version"],
      "1e ETag = catalog_version")

# If-None-Match identique -> 304
r304 = client.get("/api/app/content/meditations",
                  headers={**H, "If-None-Match": j["catalog_version"]})
check(r304.status_code == 304, "1f If-None-Match courant -> 304 (pas de retéléchargement)")

# une édition change le catalog_version
MEDS[0]["version"] += 1
MEDS[0]["updated_at"] = datetime(2026, 9, 5, tzinfo=timezone.utc)
r2 = client.get("/api/app/content/meditations", headers=H)
check(r2.get_json()["catalog_version"] != j["catalog_version"],
      "1g toute édition change catalog_version -> invalidation du cache")

# ===========================================================================
# 2. GET /api/app/content/today — date SERVEUR Europe/Paris
# ===========================================================================
_reset()
check(client.get("/api/app/content/today").status_code == 401, "2a sans Bearer -> 401")

# on fige la date serveur (le endpoint utilise _paris_today())
FIXED = date(2026, 9, 9)
_saved_today = A._paris_today
A._paris_today = lambda now=None: FIXED

DAILY.append({"id": "d1", "content_type": "daily_thought",
              "publication_date": FIXED, "title": None,
              "text": "Respire, tout passe.", "explanation": "Petite explication.",
              "image_url": "https://cdn.auryel.app/thought.png", "is_active": True})
DAILY.append({"id": "d2", "content_type": "daily_publication",
              "publication_date": FIXED, "title": "Le mot du jour",
              "text": "Un texte plus long.", "explanation": None,
              "image_url": None, "is_active": True})
DAILY.append({"id": "d3", "content_type": "daily_thought",
              "publication_date": date(2026, 9, 8), "title": None,
              "text": "hier", "explanation": None, "image_url": None,
              "is_active": True})

r = client.get("/api/app/content/today", headers=H)
j = r.get_json()
check(r.status_code == 200
      and set(j.keys()) == {"date", "version", "daily_thought", "daily_publication"},
      "2b contrat = date / version / daily_thought / daily_publication")
check(j["date"] == "2026-09-09", "2c date = jour serveur Europe/Paris, jamais le client")
check(j["daily_thought"]["text"] == "Respire, tout passe."
      and j["daily_thought"]["explanation"] == "Petite explication."
      and j["daily_thought"]["image_url"].startswith("https://"),
      "2d daily_thought : text + explanation + visuel")
check(j["daily_publication"]["title"] == "Le mot du jour"
      and j["daily_publication"]["text"] == "Un texte plus long.",
      "2e daily_publication : titre + texte")

# type absent -> null
DAILY[:] = [d for d in DAILY if d["content_type"] != "daily_publication"]
j = client.get("/api/app/content/today", headers=H).get_json()
check(j["daily_publication"] is None and j["daily_thought"] is not None,
      "2f type absent -> null (l'app garde son dernier contenu valide)")

# inactif -> null
DAILY[0]["is_active"] = False
j = client.get("/api/app/content/today", headers=H).get_json()
check(j["daily_thought"] is None, "2g contenu inactif -> null")
A._paris_today = _saved_today

# ===========================================================================
# 3. Admin — auth + CSRF
# ===========================================================================
_reset()
# pas connecté
with client.session_transaction() as s:
    s.clear()
r = client.post("/admin/content/meditation", json={"slug": "x"})
check(r.status_code in (401, 403), "3a admin non connecté -> refus")

# connecté mais sans CSRF
with client.session_transaction() as s:
    s["admin_logged"] = True
    s["csrf_token"] = "tok-abc"
r = client.post("/admin/content/meditation",
                json={"slug": "respiration_calme", "title": "Respiration",
                      "audio_url": "https://cdn.auryel.app/a.m4a"})
check(r.status_code == 403, "3b POST sans X-CSRF-Token -> 403")

HC = {"X-CSRF-Token": "tok-abc"}

# ===========================================================================
# 4. Admin — upsert méditation + validation URL
# ===========================================================================
r = client.post("/admin/content/meditation", headers=HC,
                json={"slug": "Bad Slug!", "title": "t",
                      "audio_url": "https://cdn.auryel.app/a.m4a"})
check(r.status_code == 400 and r.get_json()["error"] == "invalid_slug",
      "4a slug invalide -> 400")
r = client.post("/admin/content/meditation", headers=HC,
                json={"slug": "respiration_calme", "title": "Respiration",
                      "audio_url": "http://cdn.auryel.app/a.m4a"})
check(r.status_code == 400 and r.get_json()["error"] == "audio_url_not_https",
      "4b audio_url non HTTPS -> 400")
r = client.post("/admin/content/meditation", headers=HC,
                json={"slug": "respiration_calme", "title": "Respiration",
                      "audio_url": "https://cdn.auryel.app/a.m4a",
                      "category": "respiration", "duration_seconds": 200,
                      "sort_order": 3})
j = r.get_json()
check(r.status_code == 200 and j["ok"] and j["version"] == 1,
      "4c création méditation -> ok, version 1")
check(len(MEDS) == 1 and MEDS[0]["slug"] == "respiration_calme"
      and MEDS[0]["duration_seconds"] == 200, "4d ligne persistée")
# ré-upsert même slug -> version 2, pas de doublon
r = client.post("/admin/content/meditation", headers=HC,
                json={"slug": "respiration_calme", "title": "Respiration v2",
                      "audio_url": "https://cdn.auryel.app/a2.m4a"})
check(r.get_json()["version"] == 2 and len(MEDS) == 1
      and MEDS[0]["title"] == "Respiration v2",
      "4e ré-upsert même slug -> version++ (cache client invalidé), pas de doublon")

# toggle
mid = MEDS[0]["id"]
check(client.post("/admin/content/meditation/toggle", headers=HC,
                  json={"id": "pas-un-uuid", "is_active": False}).status_code == 400,
      "4f toggle id invalide -> 400")
check(client.post("/admin/content/meditation/toggle", headers=HC,
                  json={"id": "00000000-0000-4000-8000-000000000000",
                        "is_active": False}).status_code == 404,
      "4g toggle id inconnu -> 404")
r = client.post("/admin/content/meditation/toggle", headers=HC,
                json={"id": mid, "is_active": False})
check(r.status_code == 200 and MEDS[0]["is_active"] is False,
      "4h toggle désactive la méditation")

# ===========================================================================
# 5. Admin — upsert contenu du jour
# ===========================================================================
r = client.post("/admin/content/daily", headers=HC,
                json={"content_type": "nope", "publication_date": "2026-09-10",
                      "text": "x"})
check(r.status_code == 400 and r.get_json()["error"] == "invalid_content_type",
      "5a content_type hors allowlist -> 400")
r = client.post("/admin/content/daily", headers=HC,
                json={"content_type": "daily_thought",
                      "publication_date": "10/09/2026", "text": "x"})
check(r.status_code == 400 and r.get_json()["error"] == "invalid_publication_date",
      "5b date non ISO -> 400")
r = client.post("/admin/content/daily", headers=HC,
                json={"content_type": "daily_thought",
                      "publication_date": "2026-09-10", "text": "  "})
check(r.status_code == 400 and r.get_json()["error"] == "invalid_text",
      "5c texte vide -> 400")
r = client.post("/admin/content/daily", headers=HC,
                json={"content_type": "daily_thought",
                      "publication_date": "2026-09-10",
                      "text": "Respire.", "explanation": "Pourquoi.",
                      "image_url": "https://cdn.auryel.app/v.png"})
check(r.status_code == 200 and r.get_json()["ok"]
      and len(DAILY) == 1 and DAILY[0]["text"] == "Respire.",
      "5d création contenu du jour")
# unique (type, date) : ré-upsert met à jour, pas de doublon
r = client.post("/admin/content/daily", headers=HC,
                json={"content_type": "daily_thought",
                      "publication_date": "2026-09-10", "text": "Respire mieux."})
check(len(DAILY) == 1 and DAILY[0]["text"] == "Respire mieux.",
      "5e ré-upsert même (type, date) -> mise à jour, jamais de doublon")

# ===========================================================================
# 6. Page admin HTML rendue
# ===========================================================================
r = client.get("/admin/content")
check(r.status_code == 200 and b"Contenu distant" in r.data
      and b"csrf-token" in r.data,
      "6 GET /admin/content -> page HTML avec jeton CSRF")

# ===========================================================================
# 8. CATALOGUE 100 % DISTANT & EXTENSIBLE — aucune dépendance à une version
#    d'app : une nouvelle ligne backend apparaît dans le catalogue mobile,
#    l'ETag change, l'app se resynchronise sans release.
# ===========================================================================

# 8a — catalogue VIDE : 200, meditations == [], catalog_version stable.
_reset()
r = client.get("/api/app/content/meditations", headers=H)
j = r.get_json()
check(r.status_code == 200 and j["meditations"] == []
      and isinstance(j["catalog_version"], str) and j["catalog_version"],
      "8a catalogue vide -> 200, meditations: [], catalog_version présent")
empty_tag = j["catalog_version"]
check(client.get("/api/app/content/meditations", headers=H).get_json()["catalog_version"]
      == empty_tag, "8b catalog_version d'un catalogue vide est stable")

# 8c — 50 méditations seedées, puis AJOUT d'une 51e via l'admin :
#      elle apparaît dans le catalogue mobile + ETag différent, SANS toucher
#      quoi que ce soit côté Flutter.
_reset()
for i in range(1, 51):
    _seed_med(f"meditation-{i:02d}", sort_order=i)
r = client.get("/api/app/content/meditations", headers=H)
j50 = r.get_json()
tag50 = j50["catalog_version"]
etag50_header = r.headers.get("ETag")
check(len(j50["meditations"]) == 50, "8c 50 méditations servies")
check(etag50_header and etag50_header.strip('"') == tag50, "8c' ETag == catalog_version")

with client.session_transaction() as s:
    s["admin_logged"] = True
    s["csrf_token"] = "tok-abc"
HC8 = {"X-CSRF-Token": "tok-abc"}
r = client.post("/admin/content/meditation", headers=HC8, json={
    "slug": "meditation-51-nouvelle",
    "title": "Méditation 51 — ajoutée sans release",
    "audio_url": "https://cdn.auryel.app/51.mp3",
    "category": "lacher-prise", "duration_seconds": 300, "sort_order": 51,
})
check(r.status_code == 200 and r.get_json()["ok"] and r.get_json()["version"] == 1,
      "8d 51e méditation créée via /admin/content/meditation")

r = client.get("/api/app/content/meditations", headers=H)
j51 = r.get_json()
slugs51 = [m["slug"] for m in j51["meditations"]]
check(len(j51["meditations"]) == 51 and "meditation-51-nouvelle" in slugs51,
      "8e la 51e apparaît AUTOMATIQUEMENT dans le catalogue mobile (aucun code Flutter)")
check(slugs51 == sorted(slugs51, key=lambda s: int(s.split("-")[1]) if s.split("-")[1].isdigit() else 999)
      or slugs51[-1] == "meditation-51-nouvelle",
      "8e' triée en dernier (sort_order 51)")
check(j51["catalog_version"] != tag50
      and r.headers.get("ETag").strip('"') != tag50,
      "8f ETag / catalog_version CHANGENT après l'ajout")
check(client.get("/api/app/content/meditations",
                 headers={**H, "If-None-Match": tag50}).status_code == 200,
      "8g l'ancien ETag ne renvoie plus 304 (l'app retélécharge la nouvelle liste)")
check(client.get("/api/app/content/meditations",
                 headers={**H, "If-None-Match": j51["catalog_version"]}).status_code == 304,
      "8h le nouvel ETag renvoie 304 (plus de retéléchargement inutile)")

# 8i — RÉORDONNANCEMENT via l'admin : reflété dans l'ordre mobile + ETag change.
_reset()
_seed_med("zeta", sort_order=5)
_seed_med("alpha", sort_order=1)
before = client.get("/api/app/content/meditations", headers=H).get_json()
check([m["slug"] for m in before["meditations"]] == ["alpha", "zeta"],
      "8i ordre initial = sort_order ASC")
with client.session_transaction() as s:
    s["admin_logged"] = True
    s["csrf_token"] = "tok-abc"
client.post("/admin/content/meditation", headers=HC8, json={
    "slug": "zeta", "title": "Zeta", "audio_url": "https://cdn.auryel.app/z.mp3",
    "sort_order": 0,
})
after = client.get("/api/app/content/meditations", headers=H).get_json()
check([m["slug"] for m in after["meditations"]] == ["zeta", "alpha"],
      "8j changement de sort_order via l'admin -> nouvel ordre servi au mobile")
check(after["catalog_version"] != before["catalog_version"],
      "8k le réordonnancement change aussi l'ETag")

# 8l — CONTRAT MOBILE STABLE + montée compatible : chaque entrée porte EXACTEMENT
#      les clés documentées (id/title/audio_url = str non vides). Un nouveau
#      champ backend serait OPTIONNEL — le parseur Flutter ignore les clés
#      inconnues (cf. docs/meditations_catalog_contract.md).
_reset()
_seed_med("contrat", sort_order=1)
m0 = client.get("/api/app/content/meditations", headers=H).get_json()["meditations"][0]
check(set(m0.keys()) == {
    "id", "slug", "title", "description", "category", "duration_seconds",
    "audio_url", "image_url", "sort_order", "published_at", "version",
}, "8l contrat par méditation = jeu de clés stable et documenté")
check(isinstance(m0["id"], str) and m0["id"]
      and isinstance(m0["title"], str) and m0["title"]
      and isinstance(m0["audio_url"], str) and m0["audio_url"].startswith("https://")
      and isinstance(m0["slug"], str) and m0["slug"],
      "8m id / title / slug / audio_url toujours renseignés (str)")
check(m0["image_url"] is None and isinstance(m0["duration_seconds"], int),
      "8n image_url nullable, duration_seconds entier (0 accepté)")


# ===========================================================================
# 7. Migration v43 — additive (inspection de source)
# ===========================================================================
_init = inspect.getsource(A.init_db)
_code = "\n".join(l for l in _init.splitlines() if not l.lstrip().startswith("#"))
check("CREATE TABLE IF NOT EXISTS meditation_catalog" in _code
      and "CREATE TABLE IF NOT EXISTS daily_content" in _code
      and "uq_daily_content_type_date" in _code,
      "7a v43 : 2 tables IF NOT EXISTS + contrainte unique (content_type, date)")
_v43 = _code[_code.find("meditation_catalog"):]
check("DROP TABLE" not in _v43 and "ALTER TABLE meditation_catalog" not in _v43,
      "7b v43 : aucune destruction, aucun ALTER d'une table existante")
check("time_ledger" in A._ACCOUNT_DELETE_CHILD_TABLES
      and "meditation_catalog" not in A._ACCOUNT_DELETE_CHILD_TABLES,
      "7c contenu global -> hors _ACCOUNT_DELETE_CHILD_TABLES")

# ---------------------------------------------------------------------------
print("-" * 60)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
