#!/usr/bin/env python3
"""
sync_r2_media_catalog.py — lance la synchro Cloudflare R2 -> catalogues média.

    python3 scripts/sync_r2_media_catalog.py --dry-run     # aucune écriture
    python3 scripts/sync_r2_media_catalog.py               # mode réel

Toute la logique vit dans `r2_media_sync.py` (module testable, sans Flask).
Ce script ne fait que : lire l'environnement, ouvrir une connexion Postgres,
appeler `R2MediaCatalogSync.run()`, imprimer un résumé NON SENSIBLE.

Variables d'environnement requises (aucune valeur n'est jamais inventée) :
    DATABASE_URL
    R2_ACCOUNT_ID
    R2_ACCESS_KEY_ID
    R2_SECRET_ACCESS_KEY
    R2_BUCKET_NAME            (attendu : auryel-meditations)
    R2_PUBLIC_BASE_URL        (ex. https://pub-....r2.dev)

Aucun secret n'est affiché : les clés d'accès sont masquées, la secret key
n'apparaît jamais.
"""
import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import r2_media_sync as R  # noqa: E402


def _get_conn():
    try:
        import psycopg2
    except ImportError:  # pragma: no cover
        sys.exit("psycopg2 requis (déjà dans requirements.txt).")
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        sys.exit("DATABASE_URL absent.")
    return psycopg2.connect(dsn)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="n'écrit rien : liste seulement ce qui serait créé/adopté")
    args = ap.parse_args()

    cfg = R.R2Config.from_env()
    print("R2 :", json.dumps(cfg.masked(), ensure_ascii=False))
    if not cfg.configured:
        missing = [n for n, v in (
            ("R2_ACCOUNT_ID", cfg.account_id),
            ("R2_ACCESS_KEY_ID", cfg.access_key_id),
            ("R2_SECRET_ACCESS_KEY", cfg.secret_access_key),
            ("R2_BUCKET_NAME", cfg.bucket),
            ("R2_PUBLIC_BASE_URL", cfg.public_base_url),
        ) if not v]
        sys.exit("configuration R2 incomplète — variables manquantes : "
                 + ", ".join(missing))

    sync = R.R2MediaCatalogSync(_get_conn, R.R2Client(cfg))
    result = sync.run(dry_run=args.dry_run)

    print("-" * 64)
    mode = "DRY-RUN" if args.dry_run else "RÉEL"
    print(f"[{mode}] statut : {result['status']}")
    print(f"  MP3 trouvés          : {result['audio_objects']}")
    print(f"  MP4 trouvés          : {result['video_objects']}")
    print(f"  nouveaux MP3         : {result['new_audio']}")
    print(f"  nouveaux MP4         : {result['new_videos']}")
    print(f"  MP3 repris (adoptés) : {result['adopted_audio']}")
    print(f"  MP4 repris (adoptés) : {result['adopted_videos']}")
    print(f"  MP3 déjà connus      : {result['updated_audio']}")
    print(f"  MP4 déjà connus      : {result['updated_videos']}")
    print(f"  objets invalides     : {result['invalid_objects']}")
    print(f"  objets manquants R2  : {result['missing_objects']}")
    if result.get("missing"):
        for k in result["missing"]:
            print(f"     manquant : {k}")
    print(f"  erreurs              : {result['errors']}")
    if result.get("error_keys"):
        for k in result["error_keys"]:
            print(f"     erreur : {k}")

    sys.exit(0 if result["status"] in ("success", "partial", "already_running",
                                       "disabled") else 1)


if __name__ == "__main__":
    main()
