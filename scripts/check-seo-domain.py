#!/usr/bin/env python3
"""
check-seo-domain.py — Vérification SEO domaine pour auryelvoyance.com

Contrôles :
  1. Aucune occurrence de auryel.com (hors auryelvoyance.com)
  2. Aucune canonical vers netlify.app
  3. Aucune canonical vers /blog/ si les articles sont à la racine
  4. Chaque URL du sitemap correspond à un fichier ou redirect dans netlify.toml
  5. og:url == canonical sur chaque page HTML
  6. Aucune page légale ne contient un bloc de contenu modifié (vérif longueur minimale)
  7. Aucun lien interne absolu cassé (href vers ancien domaine)

Usage : python3 scripts/check-seo-domain.py
"""

import os, re, sys

BASEDIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW_DOM  = 'https://auryelvoyance.com'
NETLIFY  = os.path.join(BASEDIR, 'netlify.toml')
SITEMAP  = os.path.join(BASEDIR, 'sitemap.xml')

LEGAL_FILES = {'cgu.html', 'cgv.html', 'confidentialite.html',
               'mentions-legales.html', 'cookies.html'}
SKIP = {'preview-auryel-production.html', 'preview-auryel-redesign.html'}

html_files = sorted(
    f for f in os.listdir(BASEDIR)
    if f.endswith('.html') and f not in SKIP
)

errors   = []
warnings = []
ok       = []

# ── Construire liste des rewrites Netlify ────────────────────────────────────
redirected_paths = set()
with open(NETLIFY) as f:
    toml = f.read()
for m in re.finditer(r'from\s*=\s*"([^"]+)"', toml):
    redirected_paths.add(m.group(1))

# ── Construire liste des URLs du sitemap ─────────────────────────────────────
with open(SITEMAP) as f:
    sitemap_content = f.read()
sitemap_urls = re.findall(r'<loc>([^<]+)</loc>', sitemap_content)

# ── Contrôle 1 : aucune occurrence de auryel.com résiduelle ──────────────────
for fname in html_files:
    path = os.path.join(BASEDIR, fname)
    with open(path, encoding='utf-8') as f:
        content = f.read()
    # Cherche auryel.com qui n'est PAS précédé de "voyance"
    matches = re.findall(r'(?<!voyance)auryel\.com', content)
    if matches:
        errors.append(f'[C1] auryel.com résiduel ({len(matches)}x) : {fname}')

# Vérifier aussi llms.txt et sitemap.xml
for fname in ('llms.txt', 'sitemap.xml'):
    path = os.path.join(BASEDIR, fname)
    with open(path, encoding='utf-8') as f:
        c = f.read()
    if re.search(r'(?<!voyance)auryel\.com', c):
        errors.append(f'[C1] auryel.com résiduel : {fname}')

if not any('[C1]' in e for e in errors):
    ok.append('[C1] Aucun auryel.com résiduel dans aucun fichier')

# ── Contrôle 2 : aucune canonical vers netlify.app ───────────────────────────
for fname in html_files:
    path = os.path.join(BASEDIR, fname)
    with open(path, encoding='utf-8') as f:
        content = f.read()
    m = re.search(r'<link rel="canonical" href="([^"]+)"', content)
    if m and 'netlify.app' in m.group(1):
        errors.append(f'[C2] Canonical vers netlify.app : {fname} → {m.group(1)}')

if not any('[C2]' in e for e in errors):
    ok.append('[C2] Aucune canonical vers netlify.app')

# ── Contrôle 3 : aucune canonical vers /blog/ ────────────────────────────────
for fname in html_files:
    path = os.path.join(BASEDIR, fname)
    with open(path, encoding='utf-8') as f:
        content = f.read()
    m = re.search(r'<link rel="canonical" href="([^"]+)"', content)
    if m and '/blog/' in m.group(1):
        errors.append(f'[C3] Canonical /blog/ incorrect : {fname} → {m.group(1)}')

if not any('[C3]' in e for e in errors):
    ok.append('[C3] Aucune canonical /blog/ invalide')

# ── Contrôle 4 : chaque URL sitemap a un fichier ou redirect ─────────────────
c4_ok = True
for url in sitemap_urls:
    if not url.startswith(NEW_DOM):
        errors.append(f'[C4] URL sitemap hors domaine : {url}')
        c4_ok = False
        continue
    path_part = url[len(NEW_DOM):]  # ex: /voyance-amour-gratuite
    if path_part in ('', '/'):
        # Homepage → index.html existe
        if not os.path.exists(os.path.join(BASEDIR, 'index.html')):
            errors.append(f'[C4] Fichier manquant pour : {url}')
            c4_ok = False
        continue
    slug = path_part.lstrip('/')
    html_file = slug + '.html'
    file_exists = os.path.exists(os.path.join(BASEDIR, html_file))
    redirect_exists = path_part in redirected_paths
    if not file_exists and not redirect_exists:
        errors.append(f'[C4] Pas de fichier ni redirect pour : {url}')
        c4_ok = False

if c4_ok:
    ok.append(f'[C4] Toutes les URLs sitemap ({len(sitemap_urls)}) ont un fichier ou redirect')

# ── Contrôle 5 : og:url == canonical ─────────────────────────────────────────
c5_errors = 0
for fname in html_files:
    path = os.path.join(BASEDIR, fname)
    with open(path, encoding='utf-8') as f:
        content = f.read()
    can = re.search(r'<link rel="canonical" href="([^"]+)"', content)
    ogu = re.search(r'<meta property="og:url"[^>]+content="([^"]+)"', content)
    if can and ogu:
        if can.group(1) != ogu.group(1):
            warnings.append(
                f'[C5] og:url ≠ canonical : {fname}\n'
                f'     canonical={can.group(1)}\n'
                f'     og:url   ={ogu.group(1)}'
            )
            c5_errors += 1
    elif can and not ogu:
        # Pages légales sans og:url — acceptable
        if fname not in LEGAL_FILES:
            warnings.append(f'[C5] og:url manquant (non légal) : {fname}')
            c5_errors += 1

if c5_errors == 0:
    ok.append('[C5] og:url == canonical sur toutes les pages')

# ── Contrôle 6 : pages légales non vidées ────────────────────────────────────
c6_ok = True
for fname in LEGAL_FILES:
    path = os.path.join(BASEDIR, fname)
    if not os.path.exists(path):
        errors.append(f'[C6] Page légale manquante : {fname}')
        c6_ok = False
        continue
    size = os.path.getsize(path)
    if size < 3000:
        errors.append(f'[C6] Page légale trop courte ({size}o), contenu suspect : {fname}')
        c6_ok = False

if c6_ok:
    ok.append('[C6] Pages légales intactes (taille OK)')

# ── Contrôle 7 : aucun lien interne absolu vers ancien domaine ───────────────
old_domain_pattern = re.compile(
    r'href="https?://(www\.)?auryel\.com(?!voyance)', re.IGNORECASE
)
c7_ok = True
for fname in html_files:
    path = os.path.join(BASEDIR, fname)
    with open(path, encoding='utf-8') as f:
        content = f.read()
    if old_domain_pattern.search(content):
        errors.append(f'[C7] Lien interne absolu vers ancien domaine : {fname}')
        c7_ok = False

if c7_ok:
    ok.append('[C7] Aucun lien interne vers ancien domaine')

# ── Rapport ───────────────────────────────────────────────────────────────────
print('=' * 60)
print('  CHECK SEO DOMAIN — auryelvoyance.com')
print('=' * 60)
print()

if ok:
    print(f'✓ PASSED ({len(ok)})')
    for msg in ok:
        print(f'  {msg}')
    print()

if warnings:
    print(f'⚠ WARNINGS ({len(warnings)})')
    for msg in warnings:
        print(f'  {msg}')
    print()

if errors:
    print(f'✗ ERRORS ({len(errors)})')
    for msg in errors:
        print(f'  {msg}')
    print()
else:
    print('✗ ERRORS (0) — aucune erreur critique')
    print()

print(f'Fichiers HTML analysés : {len(html_files)}')
print(f'URLs sitemap vérifiées : {len(sitemap_urls)}')
print(f'Redirects Netlify chargés : {len(redirected_paths)}')
print()

sys.exit(1 if errors else 0)
