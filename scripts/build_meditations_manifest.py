#!/usr/bin/env python3
"""
build_meditations_manifest.py — génère le manifest NON SENSIBLE des méditations
Auryel à partir d'un dossier local de MP3 nommés `NN-slug-en-toutes-lettres.mp3`.

Ne téléverse RIEN, ne déplace/renomme/modifie AUCUN fichier source, ne contient
aucun secret. Sortie : un JSON `slug -> métadonnées` que
`scripts/import_meditations.py` consomme ensuite pour peupler `meditation_catalog`
via l'API admin (`POST /admin/content/meditation`).

Dérivations :
  - fichier `01-quand-tout-devient-trop-lourd.mp3`
      -> sort_order  = 1            (préfixe NN, sans zéro non significatif)
      -> filename     = "01-quand-tout-devient-trop-lourd.mp3"
      -> slug         = "quand-tout-devient-trop-lourd"   (partie après `NN-`)
      -> title        = "Quand tout devient trop lourd"   (MANIFESTE.txt si présent,
                        sinon dérivé du slug : mots séparés, 1re lettre capitale)
      -> category     = slug de catégorie ("stress-calme", …) depuis MANIFESTE.txt
      -> audio_url    = None  (placeholder — rempli après hébergement HTTPS réel,
                        ou par --audio-base-url au moment de l'import)
      -> duration_seconds = 0   (renseigné plus tard, réel du MP3)
      -> image_url    = None
      -> published_at = None (= publié immédiatement à l'import)

Usage :
  python3 scripts/build_meditations_manifest.py \
      --audios ~/Desktop/AURYEL_70_MEDITATIONS/audios \
      --manifeste ~/Desktop/AURYEL_70_MEDITATIONS/MANIFESTE.txt \
      --out scripts/meditations_manifest.json
"""
import argparse
import json
import os
import re
import sys

_FNAME_RE = re.compile(r"^(?P<num>\d{2,4})-(?P<slug>[a-z0-9][a-z0-9-]{1,63})\.mp3$")
_SLUG_OK_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")  # miroir _MEDITATION_SLUG_RE backend

# « Catégorie éditoriale » de MANIFESTE.txt -> slug de catégorie stable.
_CATEGORY_SLUGS = {
    "stress & calme": "stress-calme",
    "confiance": "confiance",
    "amour & relations": "amour",
    "sommeil": "sommeil",
    "motivation": "motivation",
    "rupture & manque": "rupture-manque",
    "lâcher-prise": "lacher-prise",
    "lacher-prise": "lacher-prise",
}


def _category_slug(raw):
    key = (raw or "").strip().lower()
    if key in _CATEGORY_SLUGS:
        return _CATEGORY_SLUGS[key]
    # repli : slugifier ce qu'on a (jamais vide)
    s = re.sub(r"[^a-z0-9]+", "-", key).strip("-")
    return s or ""


def _title_from_slug(slug):
    words = slug.replace("_", "-").split("-")
    if not words:
        return slug
    return " ".join(words).strip().capitalize()


def parse_manifeste(path):
    """MANIFESTE.txt : lignes `NN | code | Catégorie | Titre | NN mots`.
    Retourne { sort_order:int -> {"title": str, "category": str} }. Fichier
    absent/illisible -> dict vide (titres dérivés du slug)."""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 4 or not parts[0].isdigit():
                    continue
                out[int(parts[0])] = {
                    "title": parts[3],
                    "category": _category_slug(parts[2]),
                }
    except OSError:
        return {}
    return out


def build(audios_dir, manifeste_path):
    if not os.path.isdir(audios_dir):
        sys.exit(f"dossier audios introuvable : {audios_dir}")
    meta = parse_manifeste(manifeste_path)

    entries = []
    seen_slugs = set()
    skipped = []
    for fname in sorted(os.listdir(audios_dir)):
        m = _FNAME_RE.match(fname)
        if not m:
            if fname.lower().endswith(".mp3"):
                skipped.append(fname)
            continue
        sort_order = int(m.group("num"))
        slug = m.group("slug")
        if not _SLUG_OK_RE.match(slug):
            skipped.append(f"{fname} (slug non conforme)")
            continue
        if slug in seen_slugs:
            skipped.append(f"{fname} (slug en doublon)")
            continue
        seen_slugs.add(slug)
        mm = meta.get(sort_order, {})
        entries.append({
            "sort_order": sort_order,
            "filename": fname,
            "slug": slug,
            "title": (mm.get("title") or _title_from_slug(slug)).strip()[:200],
            "description": "",
            "category": mm.get("category", ""),
            "duration_seconds": 0,
            "audio_url": None,       # placeholder — cf. --audio-base-url à l'import
            "image_url": None,
            "published_at": None,    # None = publié immédiatement
        })

    entries.sort(key=lambda e: (e["sort_order"], e["slug"]))
    return entries, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audios", required=True,
                    help="dossier local des MP3 (NN-slug.mp3)")
    ap.add_argument("--manifeste", default="",
                    help="MANIFESTE.txt (titres/catégories) — facultatif")
    ap.add_argument("--out", required=True, help="chemin du JSON de sortie")
    args = ap.parse_args()

    entries, skipped = build(os.path.expanduser(args.audios),
                             os.path.expanduser(args.manifeste))

    payload = {
        "_comment": (
            "Manifest NON SENSIBLE des méditations Auryel (slug -> métadonnées). "
            "Généré par scripts/build_meditations_manifest.py depuis un dossier "
            "local de MP3 `NN-slug.mp3`. `audio_url` est un placeholder tant que "
            "les fichiers ne sont pas hébergés sur un stockage HTTPS "
            "(cf. docs/meditations_storage.md) — l'import fournit alors soit "
            "l'URL réelle par entrée, soit --audio-base-url. Aucun fichier "
            "binaire, aucun secret."
        ),
        "manifest_version": 1,
        "count": len(entries),
        "meditations": entries,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"{len(entries)} méditation(s) -> {args.out}")
    if skipped:
        print(f"{len(skipped)} fichier(s) .mp3 ignoré(s) (nom non conforme) :")
        for s in skipped:
            print(f"  - {s}")


if __name__ == "__main__":
    main()
