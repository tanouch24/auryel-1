#!/usr/bin/env python3
"""
import_relaxation_videos.py — peuple / met à jour `relaxation_video_catalog` à
partir d'un manifest local (`scripts/relaxation_videos_manifest.json`), via
l'API admin existante.

Ne téléverse AUCUN fichier binaire, ne stocke AUCUN secret dans le dépôt :
le mot de passe admin est passé en argument ou via `$AURYEL_ADMIN_PASSWORD`.
L'upload des vidéos sur un stockage HTTPS (cf. docs/relaxation_videos_storage.md)
est un préalable SÉPARÉ — ce script se contente de référencer des URL.

Chaîne :
  POST /admin/login  (password)                  -> cookie de session admin
  GET  /admin/content                            -> jeton CSRF (meta csrf-token)
  POST /admin/content/relaxation-video  (x N)     -> upsert idempotent par `slug`
                                                    (ON CONFLICT (slug) DO UPDATE,
                                                     version += 1). Relancer le
                                                     script ne crée jamais de
                                                     doublon.

Résolution de `video_url` pour chaque entrée :
  1. `video_url` déjà renseigné dans le manifest            -> utilisé tel quel
  2. sinon, si --video-base-url fourni : <base>/<filename>   -> construit
  3. sinon : entrée IGNORÉE (video_url est obligatoire côté backend)

Usage :
  export AURYEL_ADMIN_PASSWORD='…'
  python3 scripts/import_relaxation_videos.py \
      --base-url https://web-production-XXXXX.up.railway.app \
      --manifest scripts/relaxation_videos_manifest.json \
      --video-base-url https://media.auryel.app/relaxation-videos \
      [--dry-run] [--only-slugs a,b,c] [--timeout 20]

Sans --base-url NI --dry-run : refus (on ne devine jamais la cible).
"""
import argparse
import json
import os
import re
import sys

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("Le module `requests` est requis (déjà dans requirements.txt).")

_CSRF_META_RE = re.compile(
    r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', re.I
)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


def _resolve_video_url(entry, video_base_url):
    url = (entry.get("video_url") or "").strip()
    if url:
        return url
    if video_base_url and entry.get("filename"):
        return f"{video_base_url.rstrip('/')}/{entry['filename']}"
    return ""


def _load_manifest(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    vids = data.get("videos")
    if not isinstance(vids, list) or not vids:
        sys.exit(f"manifest vide / invalide : {path}")
    return vids


def _admin_session(base_url, password, timeout):
    s = requests.Session()
    s.headers["User-Agent"] = "auryel-import-relaxation-videos/1.0"
    s.post(f"{base_url}/admin/login", data={"password": password},
           timeout=timeout, allow_redirects=True)
    page = s.get(f"{base_url}/admin/content", timeout=timeout)
    if page.status_code == 200 and "Contenu distant" in page.text:
        m = _CSRF_META_RE.search(page.text)
        if m:
            return s, m.group(1)
    sys.exit("connexion admin refusée (mot de passe ?) — aucun jeton CSRF obtenu.")


def _payload(entry, video_url):
    tags = entry.get("tags") or []
    if isinstance(tags, (list, tuple)):
        tags = [str(t) for t in tags]
    return {
        "slug": entry["slug"],
        "title": entry["title"],
        "description": entry.get("description", "") or "",
        "category": entry.get("category", "") or "calm",
        "tags": tags,
        "video_url": video_url,
        "thumbnail_url": entry.get("thumbnail_url") or "",
        "sort_order": int(entry.get("sort_order") or 0),
        "published_at": entry.get("published_at") or "",
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest",
                    default="scripts/relaxation_videos_manifest.json")
    ap.add_argument("--base-url", default="",
                    help="URL du backend (ex. https://…up.railway.app)")
    ap.add_argument("--admin-password", default="",
                    help="mot de passe admin (défaut : $AURYEL_ADMIN_PASSWORD)")
    ap.add_argument("--video-base-url", default="",
                    help="préfixe HTTPS commun -> <base>/<filename> si l'entrée "
                         "n'a pas de video_url")
    ap.add_argument("--only-slugs", default="",
                    help="liste de slugs séparés par des virgules (sous-ensemble)")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="n'appelle rien : imprime les payloads qui seraient POSTés")
    args = ap.parse_args()

    vids = _load_manifest(args.manifest)

    only = {s.strip() for s in args.only_slugs.split(",") if s.strip()}
    if only:
        vids = [v for v in vids if v["slug"] in only]
        if not vids:
            sys.exit("aucune entrée du manifest ne correspond à --only-slugs")

    prepared, skipped = [], []
    for e in vids:
        slug = (e.get("slug") or "").strip()
        if not _SLUG_RE.match(slug):
            skipped.append((slug or "?", "slug non conforme"))
            continue
        video_url = _resolve_video_url(e, args.video_base_url)
        if not video_url:
            skipped.append((slug, "video_url absent (fournir --video-base-url "
                            "ou remplir le manifest)"))
            continue
        if not video_url.lower().startswith("https://"):
            skipped.append((slug, f"video_url non HTTPS : {video_url}"))
            continue
        prepared.append((e, _payload(e, video_url)))

    print(f"manifest : {len(vids)} entrée(s) — {len(prepared)} prête(s), "
          f"{len(skipped)} ignorée(s)")
    for slug, why in skipped:
        print(f"  ignorée : {slug} — {why}")

    if args.dry_run:
        for _, p in prepared:
            print(json.dumps(p, ensure_ascii=False))
        print(f"[dry-run] {len(prepared)} POST /admin/content/relaxation-video "
              f"simulés.")
        return

    if not args.base_url:
        sys.exit("--base-url requis hors --dry-run (aucune cible devinée).")
    password = args.admin_password or os.environ.get("AURYEL_ADMIN_PASSWORD", "")
    if not password:
        sys.exit("mot de passe admin absent : --admin-password ou "
                 "$AURYEL_ADMIN_PASSWORD.")
    base_url = args.base_url.rstrip("/")

    session, csrf = _admin_session(base_url, password, args.timeout)
    headers = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

    created = updated = failed = 0
    for e, p in prepared:
        try:
            r = session.post(f"{base_url}/admin/content/relaxation-video",
                             json=p, headers=headers, timeout=args.timeout)
        except requests.RequestException as exc:
            failed += 1
            print(f"  ✗ {p['slug']} — réseau : {type(exc).__name__}")
            continue
        if r.status_code == 200 and (r.json() or {}).get("ok"):
            ver = r.json().get("version")
            if ver == 1:
                created += 1
                print(f"  + {p['slug']} (créée, v1)")
            else:
                updated += 1
                print(f"  ~ {p['slug']} (mise à jour, v{ver})")
        else:
            failed += 1
            body = ""
            try:
                body = r.json().get("error", "")
            except Exception:
                body = r.text[:120]
            print(f"  ✗ {p['slug']} — HTTP {r.status_code} {body}")

    print(f"\nBilan : {created} créée(s), {updated} mise(s) à jour, "
          f"{failed} échec(s). (relançable : upsert idempotent par slug)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
