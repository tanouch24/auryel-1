#!/usr/bin/env python3
"""Vérifie que les liens HTML internes utilisent les URL canoniques propres."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "node_modules", "tmp"}
HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
EXTERNAL_SCHEMES = {"http", "https", "mailto", "tel", "javascript", "data"}


def html_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.html")
        if not any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts)
    )


def target_candidates(source: Path, href_path: str) -> list[Path]:
    if href_path.startswith("/"):
        target = ROOT / href_path.lstrip("/")
    else:
        target = source.parent / href_path

    if href_path in {"", "/"}:
        target = ROOT / "index"

    return [target, target.with_suffix(".html"), target / "index.html"]


errors: list[str] = []
checked = 0

for source in html_files():
    content = source.read_text(encoding="utf-8", errors="replace")
    for match in HREF_RE.finditer(content):
        href = match.group(1).strip()
        if not href or href.startswith("#") or href.startswith("//"):
            continue

        parsed = urlsplit(href)
        if parsed.scheme.lower() in EXTERNAL_SCHEMES:
            continue

        checked += 1
        if parsed.path.endswith(".html"):
            errors.append(
                f"URL_NON_CANONIQUE {source.relative_to(ROOT)}: {href}"
            )
            continue

        if not any(candidate.exists() for candidate in target_candidates(source, parsed.path)):
            errors.append(f"LIEN_CASSE {source.relative_to(ROOT)}: {href}")

if errors:
    print(f"ERREURS ({len(errors)})")
    for error in errors:
        print(f"- {error}")
    sys.exit(1)

print(
    f"OK - {checked} liens internes vérifiés ; "
    "aucune URL .html et aucun lien cassé."
)
