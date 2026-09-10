"""
test_relaxation_videos.py — vidéos apaisantes distantes (Migration v45) :
endpoint mobile + surface admin + mapping de compatibilité + extensibilité.

100 % local : psycopg2 mocké, fausse DB en mémoire modélisant
`relaxation_video_catalog`. Aucun réseau, aucun LLM. Couvre :
  - GET /api/app/content/relaxation-videos : contrat versionné, actif/publié
    seulement, tri (sort_order, id), ETag / If-None-Match -> 304, catalogue
    vide -> 200 videos:[], champs `tags` / `compatible_meditation_categories`
    / `is_generic` / `compatibility_map`.
  - compatibilité méditation<->vidéo : mapping backend, repli générique.
  - admin : upsert vidéo (version++), toggle, auth admin + CSRF, validation
    URL (HTTPS, allowlist), catégorie texte libre, tags normalisés.
  - extensibilité : 15 -> 16 -> 100 vidéos sans changement Flutter.
  - compatibilité montante : un champ JSON en plus ne casse rien.
  - migration v45 additive (inspection de source).
"""

import sys
import inspect
from datetime import datetime, timezone
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
VIDS = []
_SEQ = [0]


def _reset():
    VIDS.clear()
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

        if k.startswith("SELECT id, slug, title, description, video_url, "
                        "thumbnail_url, category, tags, sort_order, "
                        "published_at, updated_at, version FROM "
                        "relaxation_video_catalog WHERE is_active = TRUE"):
            now = datetime.now(timezone.utc)
            rows = [v for v in VIDS if v["is_active"]
                    and (v["published_at"] is None or v["published_at"] <= now)]
            rows.sort(key=lambda v: (v["sort_order"], v["id"]))
            self._rows = [(v["id"], v["slug"], v["title"], v["description"],
                           v["video_url"], v["thumbnail_url"], v["category"],
                           v["tags"], v["sort_order"], v["published_at"],
                           v["updated_at"], v["version"]) for v in rows]

        elif k.startswith("INSERT INTO relaxation_video_catalog"):
            (vid, slug, title, desc, video_url, thumb, cat, tags, sort_o,
             pub) = p
            existing = next((v for v in VIDS if v["slug"] == slug), None)
            if existing:
                existing.update(title=title, description=desc,
                                video_url=video_url, thumbnail_url=thumb,
                                category=cat, tags=list(tags), sort_order=sort_o,
                                published_at=pub,
                                updated_at=datetime.now(timezone.utc),
                                version=existing["version"] + 1)
                self._one = (existing["id"], existing["version"])
            else:
                VIDS.append({"id": vid, "slug": slug, "title": title,
                             "description": desc, "video_url": video_url,
                             "thumbnail_url": thumb, "category": cat,
                             "tags": list(tags), "sort_order": sort_o,
                             "is_active": True, "published_at": pub,
                             "updated_at": datetime.now(timezone.utc),
                             "version": 1})
                self._one = (vid, 1)

        elif k.startswith("UPDATE relaxation_video_catalog SET is_active=%s"):
            is_active, vid = p
            v = next((x for x in VIDS if x["id"] == vid), None)
            if v:
                v["is_active"] = is_active
                v["version"] += 1
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif k.startswith("SELECT id, slug, title, category, tags, video_url"):
            self._rows = [(v["id"], v["slug"], v["title"], v["category"],
                           v["tags"], v["video_url"], v["thumbnail_url"],
                           v["sort_order"], v["is_active"], v["published_at"],
                           v["version"]) for v in VIDS]

        # tables voisines interrogées par la page admin -> vides ici
        elif k.startswith("SELECT id, slug, title, category, duration_seconds"):
            self._rows = []
        elif k.startswith("SELECT id, content_type, publication_date, title, text"):
            self._rows = []
        elif k.startswith("INSERT INTO admin_logs"):
            self.rowcount = 1
        else:
            raise AssertionError("SQL non géré par le fake vidéos : " + k)


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


def _seed_vid(slug, sort_order=0, is_active=True, published_at=None,
              category="calm", tags=None, video="https://cdn.auryel.app/v.mp4",
              thumb=None, version=1):
    _SEQ[0] += 1
    n = _SEQ[0]
    VIDS.append({
        "id": f"{n:08d}-0000-4000-8000-000000000000",
        "slug": slug, "title": slug.replace("-", " ").title(),
        "description": "", "video_url": video, "thumbnail_url": thumb,
        "category": category, "tags": list(tags or []), "sort_order": sort_order,
        "is_active": is_active, "published_at": published_at,
        "updated_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "version": version,
    })


# ===========================================================================
# 0. Compatibilité méditation <-> vidéo (config backend, unité)
# ===========================================================================
compat, generic = A._relaxation_video_compat("ocean", [])
check("stress-calme" in compat and "sommeil" in compat and generic is False,
      "0a 'ocean' compatible stress-calme + sommeil, non générique")
compat, generic = A._relaxation_video_compat("calm", [])
check(compat == [] and generic is True,
      "0b 'calm' -> générique (compatible avec tout, aucune restriction)")
compat, generic = A._relaxation_video_compat("licorne-arc-en-ciel", [])
check(generic is True,
      "0c catégorie inconnue qui ne matche rien -> générique (repli)")
compat, generic = A._relaxation_video_compat("neutre", ["forest", "birds"])
check("stress-calme" in compat and "confiance" in compat and generic is False,
      "0d compatibilité peut venir d'un TAG, pas seulement de la catégorie")

# ===========================================================================
# 1. GET /api/app/content/relaxation-videos — auth + contrat
# ===========================================================================
_reset()
check(client.get("/api/app/content/relaxation-videos").status_code == 401,
      "1a sans Bearer -> 401")

_seed_vid("b-ocean", sort_order=2, category="ocean")
_seed_vid("a-rain", sort_order=1, category="rain")
_seed_vid("inactif", sort_order=0, is_active=False)
_seed_vid("futur", sort_order=0,
          published_at=datetime(2999, 1, 1, tzinfo=timezone.utc))
r = client.get("/api/app/content/relaxation-videos", headers=H)
j = r.get_json()
check(r.status_code == 200, "1b 200 avec Bearer")
check([v["slug"] for v in j["videos"]] == ["a-rain", "b-ocean"],
      "1c inactif + futur exclus ; tri (sort_order, id)")
check(j["version"] == A._CONTENT_API_VERSION and isinstance(j["catalog_version"], str),
      "1d version + catalog_version présents")
v0 = j["videos"][0]
check(set(v0) >= {"id", "slug", "title", "description", "video_url",
                  "thumbnail_url", "category", "tags", "sort_order",
                  "published_at", "version",
                  "compatible_meditation_categories", "is_generic"},
      "1e chaque vidéo porte le contrat complet")
check(j["generic_category"] == "calm" and isinstance(j["compatibility_map"], dict)
      and "stress-calme" in j["compatibility_map"],
      "1f compatibility_map + generic_category exposés (transparence)")
check(r.headers.get("ETag") == f'"{j["catalog_version"]}"',
      "1g en-tête ETag = catalog_version")

# ===========================================================================
# 2. ETag / If-None-Match -> 304
# ===========================================================================
tag = j["catalog_version"]
r304 = client.get("/api/app/content/relaxation-videos",
                  headers={**H, "If-None-Match": f'"{tag}"'})
check(r304.status_code == 304 and not (r304.data or b"").strip(),
      "2a If-None-Match == catalog_version -> 304, pas de retéléchargement")
check(r304.headers.get("ETag") == f'"{tag}"', "2b 304 renvoie quand même l'ETag")
r200 = client.get("/api/app/content/relaxation-videos",
                  headers={**H, "If-None-Match": '"obsolete"'})
check(r200.status_code == 200, "2c ETag périmé -> 200 complet")

# ===========================================================================
# 3. Catalogue VIDE — le système fonctionne sans aucune vidéo
# ===========================================================================
_reset()
r = client.get("/api/app/content/relaxation-videos", headers=H)
j = r.get_json()
check(r.status_code == 200 and j["videos"] == []
      and isinstance(j["catalog_version"], str) and j["catalog_version"],
      "3a catalogue vide -> 200, videos: [], catalog_version présent")
empty_tag = j["catalog_version"]
check(client.get("/api/app/content/relaxation-videos", headers=H)
      .get_json()["catalog_version"] == empty_tag,
      "3b catalog_version d'un catalogue vide est stable")

# ===========================================================================
# 4. Compatibilité exposée par vidéo + repli générique
# ===========================================================================
_reset()
_seed_vid("v-ocean", sort_order=1, category="ocean")
_seed_vid("v-generique", sort_order=2, category="calm")
_seed_vid("v-tag", sort_order=3, category="autre", tags=["night", "stars"])
j = client.get("/api/app/content/relaxation-videos", headers=H).get_json()
by = {v["slug"]: v for v in j["videos"]}
check("sommeil" in by["v-ocean"]["compatible_meditation_categories"]
      and by["v-ocean"]["is_generic"] is False,
      "4a vidéo 'ocean' : compatible sommeil, non générique")
check(by["v-generique"]["is_generic"] is True,
      "4b vidéo 'calm' : is_generic == true (utilisable pour toutes)")
check("sommeil" in by["v-tag"]["compatible_meditation_categories"],
      "4c compatibilité déduite d'un TAG ('night'/'stars' -> sommeil)")

# ===========================================================================
# 5. Admin — auth + CSRF
# ===========================================================================
_reset()
with client.session_transaction() as s:
    s.clear()
check(client.post("/admin/content/relaxation-video",
                  json={"slug": "x"}).status_code in (401, 403),
      "5a admin non connecté -> refus")
with client.session_transaction() as s:
    s["admin_logged"] = True
    s["csrf_token"] = "tok-abc"
check(client.post("/admin/content/relaxation-video",
                  json={"slug": "v-ocean-01", "title": "T",
                        "video_url": "https://cdn.auryel.app/v.mp4"}
                  ).status_code == 403,
      "5b POST sans X-CSRF-Token -> 403")
HC = {"X-CSRF-Token": "tok-abc"}

# ===========================================================================
# 6. Admin — upsert vidéo + validation
# ===========================================================================
r = client.post("/admin/content/relaxation-video", headers=HC,
                json={"slug": "Bad Slug!", "title": "t",
                      "video_url": "https://cdn.auryel.app/v.mp4"})
check(r.status_code == 400 and r.get_json()["error"] == "invalid_slug",
      "6a slug invalide -> 400")
r = client.post("/admin/content/relaxation-video", headers=HC,
                json={"slug": "v-ocean-01", "title": "Océan",
                      "video_url": "http://cdn.auryel.app/v.mp4"})
check(r.status_code == 400 and r.get_json()["error"] == "video_url_not_https",
      "6b video_url non HTTPS -> 400")
r = client.post("/admin/content/relaxation-video", headers=HC,
                json={"slug": "v-ocean-01", "title": "Océan",
                      "video_url": "https://cdn.auryel.app/v.mp4",
                      "category": "OCEAN", "tags": ["Waves", "bad tag!", "night"],
                      "sort_order": 3})
j = r.get_json()
check(r.status_code == 200 and j["ok"] and j["version"] == 1,
      "6c création vidéo -> ok, version 1")
check(len(VIDS) == 1 and VIDS[0]["category"] == "ocean"
      and VIDS[0]["tags"] == ["waves", "night"],
      "6d catégorie minusculisée, tags normalisés ('bad tag!' rejeté)")
r = client.post("/admin/content/relaxation-video", headers=HC,
                json={"slug": "v-ocean-01", "title": "Océan v2",
                      "video_url": "https://cdn.auryel.app/v2.mp4"})
check(r.get_json()["version"] == 2 and len(VIDS) == 1
      and VIDS[0]["title"] == "Océan v2",
      "6e ré-upsert même slug -> version++ (cache client invalidé), pas de doublon")
r = client.post("/admin/content/relaxation-video", headers=HC,
                json={"slug": "v-sans-categorie", "title": "Sans catégorie",
                      "video_url": "https://cdn.auryel.app/w.mp4"})
check(r.status_code == 200
      and next(v for v in VIDS if v["slug"] == "v-sans-categorie")["category"] == "calm",
      "6f catégorie absente -> défaut 'calm'")
r = client.post("/admin/content/relaxation-video", headers=HC,
                json={"slug": "v-libre", "title": "Catégorie libre",
                      "video_url": "https://cdn.auryel.app/x.mp4",
                      "category": "aurore-boreale"})
check(r.status_code == 200
      and next(v for v in VIDS if v["slug"] == "v-libre")["category"] == "aurore-boreale",
      "6g catégorie = TEXTE LIBRE (pas d'enum fermé)")

# toggle
vid = next(v for v in VIDS if v["slug"] == "v-ocean-01")["id"]
check(client.post("/admin/content/relaxation-video/toggle", headers=HC,
                  json={"id": "pas-un-uuid"}).status_code == 400,
      "6h toggle id invalide -> 400")
check(client.post("/admin/content/relaxation-video/toggle", headers=HC,
                  json={"id": "00000000-0000-4000-8000-000000000000",
                        "is_active": False}).status_code == 404,
      "6i toggle id inconnu -> 404")
ver_before = next(v for v in VIDS if v["id"] == vid)["version"]
r = client.post("/admin/content/relaxation-video/toggle", headers=HC,
                json={"id": vid, "is_active": False})
vrow = next(v for v in VIDS if v["id"] == vid)
check(r.status_code == 200 and vrow["is_active"] is False
      and vrow["version"] == ver_before + 1,
      "6j toggle désactive + version++")

# ===========================================================================
# 7. Page admin HTML — section vidéos rendue
# ===========================================================================
with client.session_transaction() as s:
    s["admin_logged"] = True
    s["csrf_token"] = "tok-abc"
r = client.get("/admin/content")
check(r.status_code == 200 and b"Contenu distant" in r.data
      and b"csrf-token" in r.data
      and "Vidéos apaisantes".encode() in r.data,
      "7 GET /admin/content -> page HTML avec la section vidéos + jeton CSRF")

# ===========================================================================
# 8. Toggle change l'ETag (le client se resynchronise)
# ===========================================================================
_reset()
_seed_vid("v1", sort_order=1, category="ocean")
_seed_vid("v2", sort_order=2, category="rain")
tag_a = client.get("/api/app/content/relaxation-videos", headers=H).get_json()["catalog_version"]
with client.session_transaction() as s:
    s["admin_logged"] = True
    s["csrf_token"] = "tok-abc"
client.post("/admin/content/relaxation-video/toggle", headers=HC,
            json={"id": VIDS[0]["id"], "is_active": False})
j_b = client.get("/api/app/content/relaxation-videos", headers=H).get_json()
check(j_b["catalog_version"] != tag_a and len(j_b["videos"]) == 1,
      "8 désactivation -> ETag différent + vidéo retirée du set servi")

# ===========================================================================
# 9. EXTENSIBILITÉ — 15 -> 16 -> 100 vidéos, zéro changement Flutter
# ===========================================================================
_reset()
for i in range(1, 16):
    _seed_vid(f"relaxation-{i:03d}", sort_order=i,
              category="calm" if i % 2 else "ocean")
j15 = client.get("/api/app/content/relaxation-videos", headers=H).get_json()
tag15 = j15["catalog_version"]
check(len(j15["videos"]) == 15, "9a 15 vidéos servies")
# le backend renvoie une 16e : reconnue immédiatement, ETag différent
with client.session_transaction() as s:
    s["admin_logged"] = True
    s["csrf_token"] = "tok-abc"
r = client.post("/admin/content/relaxation-video", headers=HC,
                json={"slug": "relaxation-016", "title": "Ambiance apaisante 16",
                      "video_url": "https://cdn.auryel.app/16.mp4",
                      "category": "forest", "sort_order": 16})
check(r.status_code == 200 and r.get_json()["version"] == 1, "9b 16e vidéo créée côté admin")
j16 = client.get("/api/app/content/relaxation-videos", headers=H).get_json()
check(len(j16["videos"]) == 16
      and any(v["slug"] == "relaxation-016" for v in j16["videos"])
      and j16["catalog_version"] != tag15,
      "9c 16e vidéo présente dans le catalogue mobile + ETag changé, sans release")
# monter à 100
for i in range(17, 101):
    _seed_vid(f"relaxation-{i:03d}", sort_order=i, category="calm")
j100 = client.get("/api/app/content/relaxation-videos", headers=H).get_json()
check(len(j100["videos"]) == 100, "9d 100 vidéos servies — aucun plafond codé")
check([v["sort_order"] for v in j100["videos"]] == list(range(1, 101)),
      "9e tri déterministe conservé à 100 entrées")

# ===========================================================================
# 10. COMPATIBILITÉ MONTANTE — un champ JSON en plus ne casse aucune ancienne
#     app (le parseur mobile ignore les clés inconnues). On vérifie ici que la
#     réponse reste un sur-ensemble stable : clés connues toujours présentes.
# ===========================================================================
_reset()
_seed_vid("v-mont", sort_order=1, category="ocean", tags=["waves"])
j = client.get("/api/app/content/relaxation-videos", headers=H).get_json()
v = j["videos"][0]
for key in ("id", "slug", "title", "video_url", "category", "tags", "sort_order"):
    check(key in v, f"10 champ stable toujours présent : {key}")
check(v["thumbnail_url"] is None, "10 thumbnail_url absent -> null (jamais d'erreur)")

# ===========================================================================
# 11. Migration v45 — additive, aucun DROP / ALTER destructeur
# ===========================================================================
src = inspect.getsource(A.init_db)
i45 = src.find("Migration v45")
check(i45 != -1, "11a bloc 'Migration v45' présent dans init_db()")
seg_full = src[i45:i45 + 4000]
# on ne teste que le CODE, pas les commentaires (# ...) qui citent volontairement
# « aucun DROP / TRUNCATE / DELETE ».
seg_code = "\n".join(
    ln for ln in seg_full.splitlines() if not ln.lstrip().startswith("#")
)
check("CREATE TABLE IF NOT EXISTS relaxation_video_catalog" in seg_code,
      "11b crée relaxation_video_catalog (IF NOT EXISTS)")
check("CREATE INDEX IF NOT EXISTS\n            idx_relaxation_video_catalog_active"
      in seg_code
      or "CREATE INDEX IF NOT EXISTS idx_relaxation_video_catalog_active"
      in " ".join(seg_code.split()),
      "11c index actif (IF NOT EXISTS)")
for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "ALTER TABLE accounts"):
    check(forbidden not in seg_code,
          f"11d aucune opération destructrice : {forbidden!r}")
check("REFERENCES accounts" not in seg_code,
      "11e aucune FK vers accounts (contenu global, hors suppression de compte)")

# ===========================================================================
print("-" * 68)
print(f"RÉSULTAT : {_STATE['pass']} ok / {_STATE['fail']} ko")
sys.exit(1 if _STATE["fail"] else 0)
