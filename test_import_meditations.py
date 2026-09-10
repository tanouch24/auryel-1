"""
test_import_meditations.py — scripts de contenu méditations (hors ligne).

Couvre :
  - build_meditations_manifest.py : dérivation slug/sort_order/title/category,
    fichiers non conformes ignorés, tri déterministe, pas de doublon de slug.
  - import_meditations.py : résolution audio_url (manifest > --audio-base-url >
    ignorée), payload conforme au contrat /admin/content/meditation, validation
    slug, --dry-run n'appelle rien.
  - scripts/meditations_manifest.json : 50 entrées, slugs conformes au regex
    backend, sort_order 1..50 unique, aucun audio_url réel (placeholder).

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


BM = _load("build_meditations_manifest.py")
IM = _load("import_meditations.py")

_S = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _S["pass"] += 1
        print(f"OK  {label}")
    else:
        _S["fail"] += 1
        print(f"XX  {label}")


# ---------------------------------------------------------------------------
# 1. build_meditations_manifest — dérivations
# ---------------------------------------------------------------------------
check(BM._title_from_slug("quand-tout-devient-trop-lourd")
      == "Quand tout devient trop lourd", "1a titre dérivé du slug (capitale initiale)")
check(BM._category_slug("Stress & calme") == "stress-calme", "1b catégorie -> slug connu")
check(BM._category_slug("Sommeil") == "sommeil", "1c catégorie -> slug connu (2)")
check(BM._category_slug("Nouvelle Rubrique !") == "nouvelle-rubrique",
      "1d catégorie inconnue -> slugifiée, jamais vide")

with tempfile.TemporaryDirectory() as d:
    for fn in ("01-quand-tout-devient-trop-lourd.mp3",
               "10-se-recentrer-en-quelques-minutes.mp3",
               "02-revenir-au-calme.mp3",
               "notes.txt", "README",
               "bad name.mp3", "99-UPPER-Slug.mp3"):
        open(os.path.join(d, fn), "w").close()
    manif = os.path.join(d, "MANIFESTE.txt")
    with open(manif, "w", encoding="utf-8") as fh:
        fh.write("01 | AB12 | Stress & calme | Quand tout devient trop lourd | 400 mots\n")
        fh.write("10 | CD34 | Confiance | Se recentrer en quelques minutes | 410 mots\n")
    entries, skipped = BM.build(d, manif)

check([e["slug"] for e in entries]
      == ["quand-tout-devient-trop-lourd", "revenir-au-calme",
          "se-recentrer-en-quelques-minutes"],
      "1e slugs = partie après NN- ; tri (sort_order, slug)")
check([e["sort_order"] for e in entries] == [1, 2, 10],
      "1f sort_order dérivé du préfixe NN (sans zéro non significatif)")
check(entries[0]["title"] == "Quand tout devient trop lourd"
      and entries[0]["category"] == "stress-calme",
      "1g titre + catégorie repris de MANIFESTE.txt quand présent")
check(entries[1]["title"] == "Revenir au calme"
      and entries[1]["category"] == "",
      "1h sans MANIFESTE -> titre dérivé du slug, catégorie vide")
check(all(e["audio_url"] is None for e in entries),
      "1i audio_url = placeholder (None) — aucun upload dans ce lot")
check(any("bad name.mp3" in s for s in skipped)
      and any("99-UPPER-Slug.mp3" in s for s in skipped),
      "1j fichiers .mp3 non conformes -> ignorés, jamais importés")

# doublon de slug -> ignoré
with tempfile.TemporaryDirectory() as d:
    open(os.path.join(d, "01-repos.mp3"), "w").close()
    open(os.path.join(d, "02-repos.mp3"), "w").close()
    e2, sk2 = BM.build(d, "")
    check(len(e2) == 1 and any("doublon" in s for s in sk2),
          "1k même slug sur 2 fichiers -> une seule entrée, l'autre ignorée")

# ---------------------------------------------------------------------------
# 2. import_meditations — résolution audio_url + payload
# ---------------------------------------------------------------------------
e_no_url = {"slug": "a", "filename": "01-a.mp3", "title": "A"}
e_with_url = {"slug": "b", "filename": "02-b.mp3", "title": "B",
              "audio_url": "https://cdn.x/b.mp3"}
check(IM._resolve_audio_url(e_no_url, "https://media.x/med")
      == "https://media.x/med/01-a.mp3",
      "2a audio_url absent + --audio-base-url -> <base>/<filename>")
check(IM._resolve_audio_url(e_with_url, "https://media.x/med")
      == "https://cdn.x/b.mp3",
      "2b audio_url du manifest prioritaire sur --audio-base-url")
check(IM._resolve_audio_url(e_no_url, "") == "",
      "2c ni audio_url ni base -> '' (entrée ignorée à l'import)")

p = IM._payload(
    {"slug": "respiration-calme", "title": "Respiration", "description": "d",
     "category": "stress-calme", "duration_seconds": 200, "sort_order": 3,
     "published_at": None},
    "https://cdn.auryel.app/x.mp3")
check(set(p.keys()) == {"slug", "title", "description", "category",
                        "duration_seconds", "audio_url", "image_url",
                        "sort_order", "published_at"},
      "2d payload = exactement les champs de /admin/content/meditation")
check(p["audio_url"] == "https://cdn.auryel.app/x.mp3" and p["sort_order"] == 3
      and p["published_at"] == "",
      "2e payload : audio_url injecté, published_at None -> '' (publié maintenant)")
check(IM._SLUG_RE.match("quand-tout-devient-trop-lourd")
      and not IM._SLUG_RE.match("Bad Slug!")
      and not IM._SLUG_RE.match("x"),
      "2f regex slug = miroir _MEDITATION_SLUG_RE backend")

# --dry-run : n'appelle rien (pas de --base-url, pas de mot de passe requis)
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    json.dump({"meditations": [
        {"slug": "un", "filename": "01-un.mp3", "title": "Un"},
        {"slug": "deux", "filename": "02-deux.mp3", "title": "Deux",
         "audio_url": "https://cdn.x/2.mp3"},
    ]}, fh)
    tmp_manifest = fh.name
_argv = sys.argv
sys.argv = ["import_meditations.py", "--manifest", tmp_manifest,
            "--audio-base-url", "https://media.auryel.app/med", "--dry-run"]
try:
    IM.main()
    dry_ok = True
except SystemExit as exc:
    dry_ok = (exc.code in (None, 0))
finally:
    sys.argv = _argv
    os.unlink(tmp_manifest)
check(dry_ok, "2g --dry-run se termine proprement sans réseau ni secret")

# --dry-run sans --audio-base-url et sans audio_url -> tout ignoré, code 0
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    json.dump({"meditations": [{"slug": "un", "filename": "01-un.mp3",
                                "title": "Un"}]}, fh)
    tmp2 = fh.name
sys.argv = ["import_meditations.py", "--manifest", tmp2, "--dry-run"]
try:
    IM.main(); code = 0
except SystemExit as exc:
    code = exc.code or 0
finally:
    sys.argv = _argv
    os.unlink(tmp2)
check(code == 0, "2h --dry-run : entrées sans audio_url -> ignorées, pas d'erreur")

# ---------------------------------------------------------------------------
# 3. scripts/meditations_manifest.json — intégrité des 50 entrées
# ---------------------------------------------------------------------------
mpath = os.path.join(_ROOT, "scripts", "meditations_manifest.json")
check(os.path.isfile(mpath), "3a scripts/meditations_manifest.json présent")
data = json.load(open(mpath, encoding="utf-8"))
meds = data["meditations"]
check(data["count"] == 50 and len(meds) == 50, "3b 50 méditations")
check(all(IM._SLUG_RE.match(m["slug"]) for m in meds),
      "3c tous les slugs conformes au regex backend")
orders = sorted(m["sort_order"] for m in meds)
check(orders == list(range(1, 51)), "3d sort_order = 1..50, unique et complet")
check(len({m["slug"] for m in meds}) == 50, "3e slugs uniques")
check(all(m.get("audio_url") in (None, "") for m in meds),
      "3f aucun audio_url réel dans le manifest (placeholder) — aucun secret, aucun binaire")
check(all(m["title"] and len(m["title"]) <= 200 for m in meds),
      "3g titres non vides, <= 200 car.")
check({m["category"] for m in meds} <= {
    "stress-calme", "confiance", "amour", "sommeil", "motivation",
    "rupture-manque", "lacher-prise", ""},
      "3h catégories = taxonomie éditoriale attendue")

print("-" * 60)
print(f"RÉSULTAT : {_S['pass']} ok / {_S['fail']} ko")
sys.exit(1 if _S["fail"] else 0)
