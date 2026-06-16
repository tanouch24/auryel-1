# Changelog lancement Auryel

## 2026-06-16 - LOT 0 - Préparation

- Branche `relance-auryel-v2` créée (depuis `main`)
- Audit initial effectué
- Fichiers parasites supprimés (commit local, pas encore poussé)

### Fichiers supprimés (commit local `relance-auryel-v2`)

| Fichier | Type | Taille |
|---------|------|--------|
| `auryel_bot.py.bak` | Backup | 107 Ko |
| `preview-auryel-production.html` | Preview dev | 63 Ko |
| `preview-auryel-redesign.html` | Preview dev | 44 Ko |
| `screenshots/` (10 PNG) | Screenshots dev | ~8 Mo |

**Total libéré :** ~8,2 Mo

### Fichiers scripts/ conservés (outils QA/dev, non utilisés en production)

| Fichier | Rôle | Décision |
|---------|------|----------|
| `scripts/capture_screenshot.py` | Wrapper Playwright — captures visuelles avant/après déploiement | ✅ Conservé |
| `scripts/check-seo-domain.py` | 7 contrôles SEO (canonicals, og:url, sitemap, pages légales, anciens domaines) — à lancer avant tout push | ✅ Conservé |

### Fichiers locaux uniquement (`.gitignore` — non commités)
- `__pycache__/` (cpython-311 + cpython-314)
- `venv/`
- `.DS_Store`

---

## Points d'attention — à traiter dans les lots suivants

### LOT 6 — Sécurité : `/reset-db` à désactiver en production
La route `POST /reset-db` dans `auryel_bot.py` exécute `reset_db()` (DROP + recréation des tables).
Elle est protégée uniquement par `CRON_SECRET` en query param — insuffisant en production.
**Action requise :** supprimer la route entièrement ou la conditionner à `DEBUG=True` uniquement.
Ne jamais merger vers `main` sans cette correction.

### Tests — Migration pytest avant modifications Stripe/webhooks/sécurité
`test_guides.py` est un script manuel sans framework (pas de `assert`, pas de `pytest`, pas de reporting).
Avant toute modification des flows Stripe, webhooks Meta ou logique de sécurité (`niveau_detresse`, `META_APP_SECRET`),
migrer vers `pytest` avec des vrais cas de test isolés et un runner CI.
**Risque actuel :** une régression sur la sécurité crise (suicide/3114) pourrait passer inaperçue.
