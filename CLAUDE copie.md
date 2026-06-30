# CLAUDE.md — Auryel

Fichier de contexte automatiquement lu par Claude Code à chaque session.
Ne pas supprimer.

---

## Projet

**Auryel** — Bot WhatsApp IA spiritualité (voyance, tarot, psaumes, guidance personnalisée)
**Objectif :** Revenus récurrents 100% automatisés — lancer SEO + ads une fois, puis hands-off
**Statut :** Code complet — phase redesign site + SEO automation + ads setup

---

## Stack technique

| Composant | Techno |
|-----------|--------|
| Backend | Flask (Python) |
| Base de données | PostgreSQL |
| IA | Groq — llama-3.3-70b |
| WhatsApp | Meta Graph API |
| Paiement | Stripe |
| Emails | Resend |
| Hébergement API | Railway |
| Hébergement static | Netlify |
| Repo | tanouch24/auryel-1 |

---

## Plugins Claude Code installés

| Plugin | Rôle | Usage |
|--------|------|-------|
| `superpowers` | Méthodologie dev — plan avant code | Toujours actif |
| `frontend-design` | Design pro anti-générique | Redesign site |
| `github` | Connexion repo GitHub | Push/pull direct |
| `episodic-memory` | Mémoire entre sessions | Contexte persistant |
| `claude-seo` | Audit SEO technique (20 sous-skills) | SEO Auryel |
| `ui-ux-pro-max` | 67 styles UI, 161 palettes, 57 typos | Design pages |
| `marketing-skills` | 41 skills marketing/copywriting | Ads + copy |
| `gsap-skills` | Animations GSAP premium | Effets site |
| `claude-ads` | Création ads Google/Meta/TikTok | Campagnes ads |
| `magic MCP` | Génération 5 variants UI (21st.dev) | Design components |

### Commandes utiles plugins
```bash
/audit          # Audit SEO complet
/magic          # Génération variants UI
/review         # Review code
/simplify       # Simplifier code
```

---

## Architecture fichiers clés

```
auryel-1/
├── index.html                    # Page d'accueil principale
├── auryel_bot.py                 # Bot Flask principal
├── blog.html                     # Hub articles SEO
├── preview-auryel-redesign.html  # Maquette redesign en cours
├── preview-auryel-production.html
├── requirements.txt
├── Procfile                      # Config Railway
└── CLAUDE.md                     # Ce fichier
```

---

## Les 5 guides personas

| Guide | Spécialité | Ton |
|-------|-----------|-----|
| Séraphine 🌸 | Amour, émotions | Doux, maternel |
| Myriam 🕊️ | Psaumes, foi | Posé, biblique |
| Naomi ⚡ | Transformation | Direct, puissant |
| Élias 🌿 | Abondance, chance | Serein, confiant |
| Ezra ✨ | Tarot, mystères | Mystérieux, cosmique |

---

## Règles métier CRITIQUES

- **`update_user_silent()`** — ne jamais remplacer par `update_user()` dans les flows de relance
- Relance time-based : J+3 / J+6 / J+9, maximum 3/cycle
- Stripe webhooks : 5 événements (checkout, subscription updated/deleted, invoice succeeded/failed)
- SEO publish : `/cron/seo-publish` → push GitHub → Netlify rebuild — convention `blog/{slug}.html`

---

## Design system

```css
--violet: #6B21A8;  --violet-clair: #A855F7;
--or: #D4AF37;      --or-clair: #F0D060;
--noir: #0D0D0D;    --noir-doux: #1A1A2E;
--creme: #FDF6E3;
/* Fonts : Cinzel (titres) + Lora (corps) */
/* Animations : GSAP ScrollTrigger */
```

---

## Variables d'environnement Railway

```env
DATABASE_URL=          GROQ_API_KEY=
META_VERIFY_TOKEN=     META_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
STRIPE_SECRET_KEY=     STRIPE_WEBHOOK_SECRET=
RESEND_API_KEY=        GITHUB_TOKEN=
GITHUB_REPO=tanouch24/auryel-1
```

---

## SEO — Règles

- 3 articles HTML/jour, rotation thématiques : tarot, psaumes, voyance, amour, chance, protection, anges
- 1 guide persona par article
- Balises obligatoires : title, meta, canonical, OG, Schema.org Article + FAQPage
- Fichiers Google à créer : `llms.txt` + `sitemap.xml`

---

## Objectifs redesign (en cours)

1. Design dark luxury + animations GSAP + effets 3D
2. Audit SEO complet `/audit` + schema.org + sitemap
3. CTAs WhatsApp + Stripe optimisés
4. Core Web Vitals + performance

---

## Statut

### Fait ✅
- Stack complète, 5 personas, 150 psaumes, 52 tarot
- Stripe webhooks, relance J+3/J+6/J+9
- 40+ pages SEO, 9 plugins Claude Code

### En cours 🔄
- Redesign site complet
- Pipeline SEO auto (Make.com → Claude API → GitHub)
- Setup ads Meta + TikTok

### À ne pas toucher ⛔
- `update_user_silent()` vs `update_user()`
- Architecture relance (max 3/cycle)
- Convention slugs SEO
