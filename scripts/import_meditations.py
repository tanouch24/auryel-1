#!/usr/bin/env python3
"""
import_meditations.py — peuple / met à jour `meditation_catalog` à partir d'un
manifest local (`scripts/meditations_manifest.json`), via l'API admin existante.

Ne téléverse AUCUN fichier binaire, ne stocke AUCUN secret dans le dépôt :
le mot de passe admin est passé en argument ou via `$AURYEL_ADMIN_PASSWORD`.
L'upload des MP3 sur un stockage HTTPS (cf. docs/meditations_storage.md) est un
préalable SÉPARÉ — ce script se contente de référencer des URL.

Chaîne :
  POST /admin/login  (password)            -> cookie de session admin
  GET  /admin/content                      -> jeton CSRF (meta name="csrf-token")
  POST /admin/content/meditation  (x N)    -> upsert idempotent par `slug`
                                             (ON CONFLICT (slug) DO UPDATE,
                                              version += 1). Relancer le script
                                             ne crée jamais de doublon.

Résolution de `audio_url` pour chaque entrée :
  1. `audio_url` déjà renseigné dans le manifest            -> utilisé tel quel
  2. sinon, si --audio-base-url fourni : <base>/<filename>   -> construit
  3. sinon : entrée IGNORÉE (audio_url est obligatoire côté backend)

Usage :
  export AURYEL_ADMIN_PASSWORD='…'
  python3 scripts/import_meditations.py \
      --base-url https://web-production-XXXXX.up.railway.app \
      --manifest scripts/meditations_manifest.json \
      --audio-base-url https://media.auryel.app/meditations \
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


def _resolve_audio_url(entry, audio_base_url):
    url = (entry.get("audio_url") or "").strip()
    if url:
        return url
    if audio_base_url:
        return f"{audio_base_url.rstrip('/')}/{entry['filename']}"
    return ""


def _load_manifest(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    meds = data.get("meditations")
    if not isinstance(meds, list) or not meds:
        sys.exit(f"manifest vide / invalide : {path}")
    return meds


def _admin_session(base_url, password, timeout):
    s = requests.Session()
    s.headers["User-Agent"] = "auryel-import-meditations/1.0"
    r = s.post(f"{base_url}/admin/login", data={"password": password},
               timeout=timeout, allow_redirects=True)
    # /admin/login renvoie 200 (page) en cas d'échec, ou redirige vers /admin.
    page = s.get(f"{base_url}/admin/content", timeout=timeout)
    if page.status_code == 200 and "Contenu distant" in page.text:
        m = _CSRF_META_RE.search(page.text)
        if m:
            return s, m.group(1)
    sys.exit("connexion admin refusée (mot de passe ?) — aucun jeton CSRF obtenu.")


def _payload(entry, audio_url):
    return {
        "slug": entry["slug"],
        "title": entry["title"],
        "description": entry.get("description", "") or "",
        "category": entry.get("category", "") or "",
        "duration_seconds": int(entry.get("duration_seconds") or 0),
        "audio_url": audio_url,
        "image_url": entry.get("image_url") or "",
        "sort_order": int(entry.get("sort_order") or 0),
        "published_at": entry.get("published_at") or "",
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default="scripts/meditations_manifest.json")
    ap.add_argument("--base-url", default="",
                    help="URL du backend (ex. https://…up.railway.app)")
    ap.add_argument("--admin-password", default="",
                    help="mot de passe admin (défaut : $AURYEL_ADMIN_PASSWORD)")
    ap.add_argument("--audio-base-url", default="",
                    help="préfixe HTTPS commun -> <base>/<filename> si l'entrée "
                         "n'a pas d'audio_url")
    ap.add_argument("--only-slugs", default="",
                    help="liste de slugs séparés par des virgules (sous-ensemble)")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="n'appelle rien : imprime les payloads qui seraient POSTés")
    args = ap.parse_args()

    meds = _load_manifest(args.manifest)

    only = {s.strip() for s in args.only_slugs.split(",") if s.strip()}
    if only:
        meds = [m for m in meds if m["slug"] in only]
        if not meds:
            sys.exit("aucune entrée du manifest ne correspond à --only-slugs")

    # Préparation + validation locale.
    prepared, skipped = [], []
    for e in meds:
        slug = (e.get("slug") or "").strip()
        if not _SLUG_RE.match(slug):
            skipped.append((slug or "?", "slug non conforme"))
            continue
        audio_url = _resolve_audio_url(e, args.audio_base_url)
        if not audio_url:
            skipped.append((slug, "audio_url absent (fournir --audio-base-url "
                            "ou remplir le manifest)"))
            continue
        if not audio_url.lower().startswith("https://"):
            skipped.append((slug, f"audio_url non HTTPS : {audio_url}"))
            continue
        prepared.append((e, _payload(e, audio_url)))

    print(f"manifest : {len(meds)} entrée(s) — {len(prepared)} prête(s), "
          f"{len(skipped)} ignorée(s)")
    for slug, why in skipped:
        print(f"  ignorée : {slug} — {why}")

    if args.dry_run:
        for _, p in prepared:
            print(json.dumps(p, ensure_ascii=False))
        print(f"[dry-run] {len(prepared)} POST /admin/content/meditation simulés.")
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
            r = session.post(f"{base_url}/admin/content/meditation",
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
