#!/usr/bin/env bash
#
# netlify-build.sh — Assemble le bundle public Auryel dans _site/
#
# Netlify publie UNIQUEMENT _site/ (voir netlify.toml -> [build] publish).
# Le repo garde son arborescence : Railway/backend continuent d'utiliser la racine.
#
# Principe : ALLOWLIST EXPLICITE. Aucune copie globale du repo, aucun rsync/blacklist.
# Aucun fichier backend (auryel_bot.py, test_*.py, scripts/, migrations/,
# tarot_arcana.json, tarot_mapping.json, citations_spirituelles.json,
# auryel_relances_h4_h22.json, requirements.txt, railway.json, netlify.toml, .git ...)
# n'est copie dans _site/.

set -euo pipefail

OUT="_site"

# Fichiers publics de la racine a copier tels quels.
ROOT_FILES=(
  index.html
  application.html
  blog.html
  conseillers.html
  comment-ca-marche.html
  tarifs.html
  recrutement.html
  voyance.html
  voyance-en-ligne.html
  voyance-amour.html
  voyance-gratuite.html
  voyance-ia.html
  voyance-par-chat.html
  voyance-par-whatsapp.html
  guidance-spirituelle.html
  medium-serieux.html
  flamme-jumelle.html
  heure-miroir.html
  numerologie.html
  tarot-oui-non.html
  tirage-tarot.html
  cgu.html
  cgv.html
  mentions-legales.html
  confidentialite.html
  cookies.html
  resiliation.html
  payer.html
  success.html
  inscription.html
  404.html
  style.css
  cookie-consent.js
  favicon.svg
  sitemap.xml
  robots.txt
  llms.txt
)

# Dossiers publics a copier entierement.
#   blog/   : articles SEO (le backend y commit via l'API GitHub, a la racine)
#   images/ : portraits conseillers, og-default, ET images/tarot/ (URL publique
#             utilisee par le backend pour l'envoi d'image du tirage)
ROOT_DIRS=(
  blog
  images
)

# 1. Repartir d'un _site/ propre. On ne supprime QUE le dossier genere.
rm -rf "$OUT"
mkdir -p "$OUT"

# 2. Copier les fichiers de l'allowlist. Echec immediat si un fichier manque.
for f in "${ROOT_FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "ERREUR netlify-build: fichier obligatoire manquant : $f" >&2
    exit 1
  fi
  cp "$f" "$OUT/$f"
done

# 3. Copier les dossiers de l'allowlist. Echec immediat si un dossier manque.
for d in "${ROOT_DIRS[@]}"; do
  if [ ! -d "$d" ]; then
    echo "ERREUR netlify-build: dossier obligatoire manquant : $d/" >&2
    exit 1
  fi
  cp -R "$d" "$OUT/$d"
done

# 4. Nettoyage : retirer les .DS_Store eventuellement embarques avec les dossiers.
find "$OUT" -name '.DS_Store' -delete

echo "Netlify public bundle built in _site/"
