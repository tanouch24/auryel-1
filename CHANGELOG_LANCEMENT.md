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

**Offre finale : Premium 9,90€/mois uniquement.**

| Section | Avant | Après |
|---------|-------|-------|
| Section 2 — Prix | Plusieurs prix incohérents | 9,90€/mois uniquement |
| Section 2 — Offre unique | Anciennes offres multiples | Offre Premium unique à 9,90€/mois |
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

## 2026-06-16 - LOT 3 - Audit Stripe (aucune modification de code)

### Statut général

| Point | Statut |
|-------|--------|
| Mode Stripe | ✅ `sk_live_` — production confirmé |
| `STRIPE_WEBHOOK_SECRET` | ✅ Présente sur Railway (`whsec_…`) |
| Vérification signature webhook | ✅ `construct_event` sans fallback — retourne 400 si invalide |
| Offre semestrielle dans le code | ✅ Absente — une seule ligne commentée (ligne 816), aucun Price ID |

### Price IDs réels en production

| Plan | Price ID | Statut |
|------|----------|--------|
| `mensuel` (Premium) | `price_1TiaigFbuWJZYdVOepK7JtKw` | ✅ Actif — offre unique |
| Ancienne offre supprimée | Ancien Price Stripe retiré | ⛔ Supprimé du produit et du code |
| `semestriel` | — | ✅ Absent du code et du dict `PRICES` |

### Risques documentés

#### LOT 4 — Fenêtre de blocage cron J3 / webhook Stripe ⚠️ Priorité haute
Le cron bloque l'accès WhatsApp (`etat="pause"`) dès `nb_jours == 3`.
Le déblocage arrive ensuite via `invoice.payment_succeeded` (webhook Stripe asynchrone).
**Risque :** l'utilisateur peut être bloqué quelques heures le jour J3 entre le cron et la confirmation de paiement Stripe.
**Action recommandée en LOT 4 :**
1. Passer `nb_jours == 3` à `>= 3` (absorber les crons manqués)
2. Avant de bloquer, vérifier si un paiement Stripe est en cours (`trialing` ou `active`) pour ne pas bloquer un abonné légitime

#### Basse priorité — Idempotence `checkout.session.completed`
Aucun stockage d'`event_id` Stripe. Si Stripe rejoue le webhook (timeout réseau), le message WhatsApp de bienvenue peut être envoyé en double.
L'impact DB est nul (opération SET idempotente). Seul le message WhatsApp peut être dupliqué.
**Action :** à traiter lors de la migration pytest / durcissement webhooks.

---

## Avancement lots — état au 2026-06-16

| Lot | Statut | Notes |
|-----|--------|-------|
| LOT 0 — Préparation | ✅ Déployé | Fichiers parasites supprimés, audit initial |
| LOT 1 — Pages commerciales | ✅ Déployé | Offre harmonisée, prix corrigés, navbars |
| LOT CGV | ✅ Déployé | Prix, espace membre, Auryel Inc. |
| LOT 2 — Templates Meta | 🟡 En attente | Soumis, approbation Meta en cours |
| LOT 3 — Audit Stripe | ✅ Clos | Audit uniquement, aucune modification |
| LOT 4 — Cron `>= 3` + fenêtre Stripe | 🔴 À faire | Voir risques documentés ci-dessus |
| LOT 5 — Prompt IA / 10 personas | 🔴 À faire | Le plus gros morceau restant |
| LOT 6 — Sécurité admin | 🔴 À faire | `/reset-db` + durcissement |

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
