# CLAUDE.md — Auryel

Fichier de contexte automatiquement lu par Claude Code à chaque session.
Ne pas supprimer.

---

## Projet

**Auryel** — Bot WhatsApp IA spiritualité (voyance, tarot, psaumes, guidance personnalisée)
**Objectif :** Revenus récurrents 100% automatisés — lancer SEO + ads une fois, puis hands-off
**Statut :** Site live et optimisé — phase pipeline SEO auto + ads setup

---

## URLs

| Environnement | URL |
|---------------|-----|
| Site (Netlify) | auryel.netlify.app |
| Backend (Railway) | En production ✅ |
| Repo | tanouch24/auryel-1 |

---

## Stack technique

| Composant | Techno | Statut |
|-----------|--------|--------|
| Backend | Flask (Python) | ✅ Railway en prod |
| Base de données | PostgreSQL | ✅ |
| IA | Groq — llama-3.3-70b | ✅ |
| WhatsApp | Meta Graph API | ✅ |
| Paiement | Stripe | ✅ |
| Emails | Resend | ✅ |
| Hébergement API | Railway | ✅ |
| Hébergement static | Netlify | ✅ |

---

## Plugins Claude Code installés

| Plugin | Rôle |
|--------|------|
| `superpowers` | Méthodologie dev — plan avant code, agents parallèles |
| `frontend-design` | Design pro anti-générique |
| `github` | Connexion repo GitHub |
| `episodic-memory` | Mémoire entre sessions |
| `claude-seo` | Audit SEO technique (20 sous-skills) |
| `ui-ux-pro-max` | 67 styles UI, 161 palettes, 57 typos |
| `marketing-skills` | 41 skills marketing/copywriting/CRO |
| `gsap-skills` | Animations GSAP premium |
| `claude-ads` | Création ads Google/Meta/TikTok |
| `magic MCP` | Génération 5 variants UI (21st.dev) |

---

## Architecture fichiers

```
auryel-1/
├── index.html                    # Page d'accueil ✅ redesignée + hero refondu
├── conseillers.html              # 10 conseillers ✅ personnalités émotionnelles
├── tarifs.html                   # Tarifs ✅ redesignée
├── blog.html                     # Hub articles SEO ✅ photos corrigées
├── inscription.html              # Tunnel inscription ✅ faux signaux supprimés
├── auryel_bot.py                 # Bot Flask — NE PAS TOUCHER ⛔
├── style.css                     # CSS partagé global (Cinzel+Lora+violet/or)
├── sitemap.xml                   # ✅ Sprint 1
├── robots.txt                    # ✅ Sprint 1
├── llms.txt                      # ✅ Sprint 1
├── favicon.svg                   # ✅ Sprint 1
├── netlify.toml                  # Config + redirects clean URLs
└── CLAUDE.md                     # Ce fichier
```

---

## Les 10 conseillers

Séléna, Myriam, Maïa, Orion, Ezra, Luna, Théa, Cassandre, Kaël, Raphaël.
Chacun a une description émotionnelle (.ac-desc) dans conseillers.html et index.html.
Ne pas modifier les spécialités existantes.

---

## Design system — VERROUILLÉ ⛔

```css
:root {
  --void:   #04020A;
  --gold:   #D4AF37;
  --gold-b: #F0D060;
  --cream:  #FDF6E3;
  --violet: #6B21A8;
}
/* Fonts : Cinzel + Cinzel Decorative (titres) + Lora (corps) */
/* Style : dark luxury, fond noir profond, accents or/violet */
/* Animations : GSAP ScrollTrigger + canvas particules */
```

Ne jamais revenir à Playfair Display / Tenor Sans / Raleway.

---

## Tout ce qui est fait ✅

### Sprint 1 — SEO technique (commit 54f235e)
- sitemap.xml, robots.txt, llms.txt, favicon.svg
- CSS extrait dans style.css partagé (-122KB sur 40 fichiers)
- Menu hamburger mobile sur toutes les pages
- Liens blog homepage corrigés

### Sprint 2 — Conversion (commit 0f284f7)
- Disclaimer → lien discret mentions légales
- CTAs tarifs différenciés (Tester 7j / Économiser 32% / Meilleur tarif)
- Schema.org complet : FAQPage, WebSite, Service×5, Person×10
- Clean URLs via redirects Netlify

### Redesign visuel (commits cd603c2 + f272e64)
- index + conseillers + tarifs + blog + inscription
- Cinzel+Lora, violet/or, GSAP ScrollTrigger, canvas particules, tilt 3D

### P0+P1+P2 (commit 311f7b8)
- Faux signaux supprimés ("+2000 consultations", emojis, "réponse en 2 min")
- Hero refondu : "Un espace confidentiel pour les moments où vous avez besoin d'y voir plus clair"
- Photos blog visibles (fix z-index)
- 10 personnalités émotionnelles conseillers ajoutées

---

## À faire — prochaines sessions

### Sprint 3 — SEO enrichissement
- Optimiser titres pages transactionnelles
- Attributs width/height sur toutes les images
- Canonicals harmonisés

### Appliquer design aux 40 pages articles SEO
- Les articles blog n'ont pas encore le nouveau design

### Pipeline SEO automatique
- Make.com + Claude API → 3 articles/jour automatiques
- Route /cron/seo-publish déjà en place côté Flask

### Setup ads
- Meta Ads + TikTok Ads avec claude-ads
- Landing pages dédiées

### Microsoft Clarity
- Installer le tracking comportemental pour analyser le funnel

---

## Règles métier CRITIQUES ⛔

- **`update_user_silent()`** — jamais remplacer par `update_user()` dans les flows de relance
- Relance time-based : J+3 / J+6 / J+9, maximum 3/cycle
- Stripe webhooks : 5 événements
- SEO : `/cron/seo-publish` → push GitHub → Netlify rebuild
- Convention slugs : `blog/{slug}.html`
- **`auryel_bot.py`** → NE JAMAIS MODIFIER sans instruction explicite

---

## Variables d'environnement Railway

```env
DATABASE_URL=              GROQ_API_KEY=
META_VERIFY_TOKEN=         META_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
STRIPE_SECRET_KEY=         STRIPE_WEBHOOK_SECRET=
RESEND_API_KEY=            GITHUB_TOKEN=
GITHUB_REPO=tanouch24/auryel-1
```
