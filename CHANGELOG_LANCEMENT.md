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

## 2026-06-16 - LOT CGV - Correction cgv.html

### Fichier modifié : `cgv.html`

| Section | Avant | Après |
|---------|-------|-------|
| Section 2 — Prix mensuel | 4,90€ TTC | 9,90€ TTC |
| Section 2 — Prix annuel | 29,90€ TTC pour 12 mois | 49,90€ TTC pour 12 mois |
| Section 2 — Offre semestrielle | 19,90€ TTC pour 6 mois | Supprimée (inexistante dans le produit) |
| Section 2 — Essai gratuit | Absent | "3 jours de guidance offerts sur WhatsApp, sans carte bancaire requise" |
| Section 2 — Activation | Absent | "L'accès payant ne s'active que si l'utilisateur choisit volontairement de souscrire" |
| Section 3 — Résiliation | "depuis son espace membre" | "en envoyant un message WhatsApp à son conseiller ou par email à contact@auryelvoyance.com" |
| Section 6 — Résiliation | "depuis son espace membre" | "en envoyant un message WhatsApp à son conseiller ou par email à contact@auryelvoyance.com" |

### Occurrences "Auryel Inc." restantes (hors périmètre de ce lot)
- Section 1 : "entre Auryel Inc. et tout utilisateur"
- Section 4 : "sur les serveurs d'Auryel Inc."
- Section 6 : "Auryel Inc. se réserve le droit de résilier"
- Footer : "© 2026 Auryel Inc. Tous droits réservés."
→ À traiter dans un lot dédié si décision de changer le nom légal affiché.

### Validation QA
- `test_guides.py` : ✅ 5 guides distincts, sécurité crise OK
- `scripts/check-seo-domain.py` : ✅ 7/7 PASSED, 0 erreur critique

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
