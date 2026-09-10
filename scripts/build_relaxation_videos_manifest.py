#!/usr/bin/env python3
"""
build_relaxation_videos_manifest.py — génère le manifest NON SENSIBLE des
vidéos apaisantes Auryel à partir d'un dossier local de fichiers vidéo.

Ne téléverse RIEN, ne déplace / renomme / modifie AUCUN fichier source, ne
contient aucun secret. Sortie : un JSON `slug -> métadonnées` que
`scripts/import_relaxation_videos.py` consomme ensuite pour peupler
`relaxation_video_catalog` via l'API admin
(`POST /admin/content/relaxation-video`).

Formats acceptés : .mp4 .mov .m4v .webm

Dérivations (les noms de fichiers de banques d'images — ex.
`11210466-hd_1080_1920_30fps.mp4` — ne portent AUCUNE information de contenu) :
  - sort_order    = rang (1..N) dans l'ordre de tri stable des noms de fichiers
  - filename      = nom d'origine, conservé tel quel (sert à bâtir video_url)
  - slug          = "relaxation-<id-numérique>" si le nom commence par un id
                    numérique ; sinon le nom slugifié. Toujours conforme au
                    regex backend ^[a-z0-9][a-z0-9_-]{1,63}$. Doublon -> suffixe.
  - title         = "Ambiance apaisante NN" (titre générique lisible : aucun nom
                    de fichier ne permet de déduire un vrai titre)
  - category      = "calm"  (JAMAIS déduite d'un nom ambigu — cf. consigne :
                    "si une catégorie n'est pas déductible proprement -> calm")
  - tags          = []      (à enrichir plus tard côté admin)
  - video_url     = None    (placeholder — rempli à l'import via --video-base-url
                    ou en éditant le manifest)
  - thumbnail_url = None
  - published_at  = None    (= publié immédiatement à l'import)

Usage :
  python3 scripts/build_relaxation_videos_manifest.py \
      --videos ~/Desktop/"videos apaisantes auryel" \
      --out scripts/relaxation_videos_manifest.json
"""
import argparse
import json
import os
import re
import sys

_VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm")
_SLUG_OK_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")  # miroir backend
_LEADING_NUM_RE = re.compile(r"^(?P<id>\d{3,})")
_GENERIC_CATEGORY = "calm"


def _slugify(text):
    """minuscule, accents -> ascii approx., tout le reste -> '-', borné 64."""
    import unicodedata
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = t.lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    t = re.sub(r"-{2,}", "-", t)
    return t[:64].strip("-")


def _derive_slug(stem):
    """slug stable à partir du nom de fichier (sans extension). Les vidéos de
    banque commencent par un id numérique -> `relaxation-<id>` (lisible, court,
    unique). Sinon on slugifie le nom entier."""
    m = _LEADING_NUM_RE.match(stem)
    if m:
        candidate = f"relaxation-{m.group('id')}"
    else:
        candidate = _slugify(stem) or "relaxation"
        if candidate[0].isdigit():
            candidate = f"v-{candidate}"[:64]
    if not _SLUG_OK_RE.match(candidate):
        candidate = re.sub(r"[^a-z0-9_-]", "", candidate) or "relaxation"
        if not _SLUG_OK_RE.match(candidate):
            candidate = "relaxation-video"
    return candidate


def build(videos_dir):
    if not os.path.isdir(videos_dir):
        sys.exit(f"dossier vidéos introuvable : {videos_dir}")

    files = sorted(
        f for f in os.listdir(videos_dir)
        if not f.startswith(".")
        and os.path.splitext(f)[1].lower() in _VIDEO_EXTS
        and os.path.isfile(os.path.join(videos_dir, f))
    )

    entries = []
    seen_slugs = set()
    skipped = []
    for f in files:
        stem, ext = os.path.splitext(f)
        base_slug = _derive_slug(stem)
        slug = base_slug
        n = 2
        while slug in seen_slugs:
            slug = f"{base_slug}-{n}"[:64]
            n += 1
        if not _SLUG_OK_RE.match(slug):
            skipped.append(f"{f} (slug non conforme : {slug})")
            continue
        seen_slugs.add(slug)
        sort_order = len(entries) + 1
        entries.append({
            "sort_order": sort_order,
            "filename": f,
            "format": ext.lower().lstrip("."),
            "slug": slug,
            "title": f"Ambiance apaisante {sort_order:02d}",
            "description": "",
            "category": _GENERIC_CATEGORY,
            "tags": [],
            "video_url": None,       # placeholder — cf. --video-base-url
            "thumbnail_url": None,
            "published_at": None,    # None = publié immédiatement
        })

    return entries, skipped


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--videos", required=True,
                    help="dossier local des vidéos (.mp4/.mov/.m4v/.webm)")
    ap.add_argument("--out", required=True, help="chemin du JSON de sortie")
    args = ap.parse_args()

    entries, skipped = build(os.path.expanduser(args.videos))

    formats = sorted({e["format"] for e in entries})
    payload = {
        "_comment": (
            "Manifest NON SENSIBLE des vidéos apaisantes Auryel "
            "(slug -> métadonnées). Généré par "
            "scripts/build_relaxation_videos_manifest.py depuis un dossier local "
            "de vidéos. `video_url` est un placeholder tant que les fichiers ne "
            "sont pas hébergés sur un stockage HTTPS "
            "(cf. docs/relaxation_videos_storage.md) — l'import fournit alors "
            "soit l'URL réelle par entrée, soit --video-base-url. `category` "
            "vaut 'calm' (générique) car aucun nom de fichier ne permet de "
            "déduire une ambiance. Aucun fichier binaire, aucun secret."
        ),
        "manifest_version": 1,
        "count": len(entries),
        "formats_detected": formats,
        "videos": entries,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"{len(entries)} vidéo(s) -> {args.out}")
    print(f"formats détectés : {', '.join(formats) or '(aucun)'}")
    if skipped:
        print(f"{len(skipped)} fichier(s) ignoré(s) :")
        for s in skipped:
            print(f"  - {s}")


if __name__ == "__main__":
    main()
