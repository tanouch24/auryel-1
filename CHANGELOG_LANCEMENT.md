# Changelog lancement Auryel

## 2026-06-16 - LOT 0 - Préparation

- Branche `relance-auryel-v2` créée (depuis `main`)
- Audit initial effectué (voir résumé ci-dessous)
- Fichiers parasites identifiés (liste jointe, pas encore supprimés)

### Fichiers parasites identifiés (en attente de validation)

Tous ces fichiers sont **trackés par git** (commités dans le repo) :

| Fichier | Type | Taille | Raison de suppression |
|---------|------|--------|-----------------------|
| `auryel_bot.py.bak` | Backup | 107 Ko | Résidu d'une version précédente |
| `preview-auryel-production.html` | Preview dev | 63 Ko | Fichier de prévisualisation obsolète |
| `preview-auryel-redesign.html` | Preview dev | 44 Ko | Fichier de prévisualisation obsolète |
| `screenshots/blog_desktop.png` | Screenshot | 1,4 Mo | Captures de développement, non utilisées en prod |
| `screenshots/blog_mobile.png` | Screenshot | 410 Ko | idem |
| `screenshots/home_desktop.png` | Screenshot | 1 Mo | idem |
| `screenshots/home_mobile.png` | Screenshot | 400 Ko | idem |
| `screenshots/tarifs_desktop.png` | Screenshot | 1,2 Mo | idem |
| `screenshots/tarifs_mobile.png` | Screenshot | 402 Ko | idem |
| `screenshots/tarot_desktop.png` | Screenshot | 1,1 Mo | idem |
| `screenshots/tarot_mobile.png` | Screenshot | 446 Ko | idem |
| `screenshots/voyance_desktop.png` | Screenshot | 1,1 Mo | idem |
| `screenshots/voyance_mobile.png` | Screenshot | 441 Ko | idem |
| `scripts/capture_screenshot.py` | Script dev | — | Script de capture Playwright, usage ponctuel |
| `scripts/check-seo-domain.py` | Script dev | — | Script SEO ponctuel, pas de prod |

**Total estimé libéré :** ~8,5 Mo (surtout les screenshots)

Fichiers **locaux uniquement** (déjà dans `.gitignore`, pas commités) :
- `__pycache__/` (cpython-311 + cpython-314)
- `venv/`
- `.DS_Store`
