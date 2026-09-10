"""
test_import_relaxation_videos.py — scripts de contenu vidéos apaisantes
(hors ligne).

Couvre :
  - build_relaxation_videos_manifest.py : dérivation slug (id numérique ->
    relaxation-<id>), slugify accents/espaces, dédoublonnage, filtre des
    extensions vidéo, category = "calm" par défaut, tri stable sort_order 1..N.
  - import_relaxation_videos.py : résolution video_url (manifest > --video-base-url
    > ignorée), payload conforme au contrat /admin/content/relaxation-video,
    validation slug, --dry-run n'appelle rien.
  - scripts/relaxation_videos_manifest.json : 12 entrées, slugs conformes au
    regex backend, sort_order 1..12 unique, aucun video_url réel (placeholder),
    formats détectés.

AUCUN réseau, AUCUN secret, AUCUN fichier déplacé. Script (pas pytest).
"""
import importlib.util
import json
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    path = os.path.join(_ROOT, "scripts", name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BV = _load("build_relaxation_videos_manifest.py")
IV = _load("import_relaxation_videos.py")

_S = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _S["pass"] += 1
        print(f"OK  {label}")
    else:
        _S["fail"] += 1
        print(f"XX  {label}")


# ---------------------------------------------------------------------------
# 1. build_relaxation_videos_manifest — dérivations
# ---------------------------------------------------------------------------
check(BV._derive_slug("11210466-hd_1080_1920_30fps") == "relaxation-11210466",
      "1a nom de banque (id numérique en tête) -> relaxation-<id>")
check(BV._derive_slug("14618955-uhd_2160_3840_24fps") == "relaxation-14618955",
      "1b variante uhd -> relaxation-<id>")
check(BV._derive_slug("Forêt   Brumeuse (aube)") == "foret-brumeuse-aube",
      "1c nom descriptif -> slugifié (accents, espaces, parenthèses)")
check(BV._SLUG_OK_RE.match(BV._derive_slug("2024 clip")),
      "1d slug commençant par un chiffre reste conforme au regex backend")

with tempfile.TemporaryDirectory() as d:
    for fn in ("11210466-hd_1080_1920_30fps.mp4",
               "20349634-hd_1080_1920_30fps.mp4",
               "12113828-hd_1080_1920_60fps.mp4",
               "ambiance.mov", "clip.webm", "vieux.m4v",
               "notes.txt", "cover.jpg", ".DS_Store"):
        open(os.path.join(d, fn), "w").close()
    entries, skipped = BV.build(d)

check([e["slug"] for e in entries] == [
        "relaxation-11210466", "relaxation-12113828", "relaxation-20349634",
        "ambiance", "clip", "vieux"],
      "1e .mp4/.mov/.m4v/.webm retenus, triés par nom de fichier ; .txt/.jpg ignorés")
check([e["sort_order"] for e in entries] == [1, 2, 3, 4, 5, 6],
      "1f sort_order = rang 1..N, contigu et déterministe")
check(all(e["category"] == "calm" for e in entries),
      "1g category = 'calm' par défaut (jamais déduite d'un nom ambigu)")
check(all(e["tags"] == [] for e in entries)
      and all(e["video_url"] is None for e in entries)
      and all(e["thumbnail_url"] is None for e in entries),
      "1h tags [], video_url/thumbnail_url = placeholder (None) — aucun upload")
check(all(e["title"] and len(e["title"]) <= 200 for e in entries),
      "1i titre générique lisible non vide")
check({e["format"] for e in entries} == {"mp4", "mov", "webm", "m4v"},
      "1j format détecté par entrée")

# dédoublonnage de slug (deux fichiers -> même id numérique en tête)
with tempfile.TemporaryDirectory() as d:
    open(os.path.join(d, "12345678-a.mp4"), "w").close()
    open(os.path.join(d, "12345678-b.mp4"), "w").close()
    e2, _ = BV.build(d)
    check([x["slug"] for x in e2] == ["relaxation-12345678", "relaxation-12345678-2"],
          "1k slug en collision -> suffixe -2, jamais de doublon")

# ---------------------------------------------------------------------------
# 2. import_relaxation_videos — résolution video_url + payload
# ---------------------------------------------------------------------------
e_no_url = {"slug": "a", "filename": "01-a.mp4", "title": "A"}
e_with_url = {"slug": "b", "filename": "02-b.mp4", "title": "B",
              "video_url": "https://cdn.x/b.mp4"}
check(IV._resolve_video_url(e_no_url, "https://media.x/relaxation-videos")
      == "https://media.x/relaxation-videos/01-a.mp4",
      "2a video_url absent + --video-base-url -> <base>/<filename>")
check(IV._resolve_video_url(e_with_url, "https://media.x/relaxation-videos")
      == "https://cdn.x/b.mp4",
      "2b video_url du manifest prioritaire sur --video-base-url")
check(IV._resolve_video_url(e_no_url, "") == "",
      "2c ni video_url ni base -> '' (entrée ignorée à l'import)")

p = IV._payload(
    {"slug": "relaxation-ocean", "title": "Ambiance", "description": "d",
     "category": "ocean", "tags": ["waves", "night"], "sort_order": 3,
     "published_at": None},
    "https://cdn.auryel.app/x.mp4")
check(set(p.keys()) == {"slug", "title", "description", "category", "tags",
                        "video_url", "thumbnail_url", "sort_order",
                        "published_at"},
      "2d payload = exactement les champs de /admin/content/relaxation-video")
check(p["video_url"] == "https://cdn.auryel.app/x.mp4" and p["sort_order"] == 3
      and p["published_at"] == "" and p["tags"] == ["waves", "night"],
      "2e payload : video_url injecté, published_at None -> '', tags conservés")
check(IV._payload({"slug": "x", "title": "X"}, "https://c/x.mp4")["category"] == "calm",
      "2f payload : category absente -> 'calm'")
check(IV._SLUG_RE.match("relaxation-11210466")
      and not IV._SLUG_RE.match("Bad Slug!")
      and not IV._SLUG_RE.match("x"),
      "2g regex slug = miroir _MEDITATION_SLUG_RE backend")

# --dry-run : n'appelle rien (pas de --base-url, pas de mot de passe requis)
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    json.dump({"videos": [
        {"slug": "un", "filename": "01-un.mp4", "title": "Un"},
        {"slug": "deux", "filename": "02-deux.mp4", "title": "Deux",
         "video_url": "https://cdn.x/2.mp4"},
    ]}, fh)
    tmp_manifest = fh.name
_argv = sys.argv
sys.argv = ["import_relaxation_videos.py", "--manifest", tmp_manifest,
            "--video-base-url", "https://media.auryel.app/relaxation-videos",
            "--dry-run"]
try:
    IV.main()
    dry_ok = True
except SystemExit as exc:
    dry_ok = (exc.code in (None, 0))
finally:
    sys.argv = _argv
    os.unlink(tmp_manifest)
check(dry_ok, "2h --dry-run se termine proprement sans réseau ni secret")

# --dry-run sans --video-base-url et sans video_url -> tout ignoré, code 0
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    json.dump({"videos": [{"slug": "un", "filename": "01-un.mp4",
                           "title": "Un"}]}, fh)
    tmp2 = fh.name
sys.argv = ["import_relaxation_videos.py", "--manifest", tmp2, "--dry-run"]
try:
    IV.main(); code = 0
except SystemExit as exc:
    code = exc.code or 0
finally:
    sys.argv = _argv
    os.unlink(tmp2)
check(code == 0, "2i --dry-run : entrées sans video_url -> ignorées, pas d'erreur")

# ---------------------------------------------------------------------------
# 3. scripts/relaxation_videos_manifest.json — intégrité des 12 entrées
# ---------------------------------------------------------------------------
mpath = os.path.join(_ROOT, "scripts", "relaxation_videos_manifest.json")
check(os.path.isfile(mpath), "3a scripts/relaxation_videos_manifest.json présent")
data = json.load(open(mpath, encoding="utf-8"))
vids = data["videos"]
check(data["count"] == 12 and len(vids) == 12, "3b 12 vidéos détectées")
check(all(IV._SLUG_RE.match(v["slug"]) for v in vids),
      "3c tous les slugs conformes au regex backend")
orders = sorted(v["sort_order"] for v in vids)
check(orders == list(range(1, 13)), "3d sort_order = 1..12, unique et complet")
check(len({v["slug"] for v in vids}) == 12, "3e slugs uniques")
check(all(v.get("video_url") in (None, "") for v in vids),
      "3f aucun video_url réel dans le manifest (placeholder) — aucun secret, aucun binaire")
check(all(v["title"] and len(v["title"]) <= 200 for v in vids),
      "3g titres non vides, <= 200 car.")
check(all(v["category"] == "calm" for v in vids),
      "3h toutes les catégories = 'calm' (noms de fichiers sans info d'ambiance)")
check(data["formats_detected"] == ["mp4"],
      "3i formats détectés = ['mp4'] (12 fichiers .mp4, aucun .mov/.m4v/.webm)")
check(all(v["tags"] == [] for v in vids), "3j tags vides (à enrichir en admin)")

print("-" * 60)
print(f"RÉSULTAT : {_S['pass']} ok / {_S['fail']} ko")
sys.exit(1 if _S["fail"] else 0)
