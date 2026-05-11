# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Auryel** is a spiritual guidance SaaS chatbot delivered via WhatsApp. Users consult one of 5 AI-powered "guides" (Séraphine, Myriam, Naomi, Élias, Ezra) for divination (tarot, psalms, numerology). The platform uses a freemium model: 7 free exchanges, then Stripe subscription (€4.90/mo, €19.90/semester, €29.90/year).

**Dual-host deployment:**
- **Railway.io** — Python/Flask backend (`Procfile`: `gunicorn auryel_bot:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`)
- **Netlify** — 40 static HTML landing pages (auto-deploys on git push)

## Running Locally

```bash
pip install -r requirements.txt
python auryel_bot.py       # dev server (Flask debug mode)
# or
gunicorn auryel_bot:app    # production-like
```

Required env vars (all must be set — app verifies at startup):
```
SECRET_KEY, VERIFY_TOKEN, ADMIN_PASSWORD, CRON_SECRET,
DATABASE_URL, STRIPE_SK, STRIPE_WEBHOOK_SECRET,
WHATSAPP_TOKEN, PHONE_NUMBER_ID, GROQ_API_KEY, RESEND_API_KEY
```

No build step. No test suite. No linter configured.

## Architecture

The entire backend is a **single file**: `auryel_bot.py`. It's monolithic but sectioned:

### Request Flow
```
WhatsApp POST /webhook
  → extract phone + message text
  → upsert user in PostgreSQL (users table)
  → check subscription state (etat: normal / attente_paiement / pause)
  → detect signals in text: email, first name, budget objection, end-of-conversation
  → fetch last 20 messages from PostgreSQL (messages table)
  → detect divination tool request → pull psalm/tarot/numerology content
  → call Groq LLM (llama-3.3-70b-versatile, max 180 tokens, temp=0.92)
  → 35% chance: append Stripe checkout link (after 3+ exchanges)
  → save reply to messages table, increment nb_echanges
  → send reply via WhatsApp Business API
```

### Key Sections in `auryel_bot.py`
- **Database init** (`init_db`) — creates `users` + `messages` tables if absent
- **`get_reply()`** — core LLM call; builds system prompt from guide personality + engagement level
- **`POST /webhook`** — main message handler; all detection logic lives here
- **`POST /stripe/webhook`** — handles `checkout.session.completed`, `customer.subscription.*` events
- **`GET/POST /cron/daily`** — Day 6 upsell, Day 7 pause, Day 8 final nudge; subscriber re-engagement every 3 days (max 3 times)
- **`GET/POST /cron/seo-publish`** — Groq generates article → Markdown→HTML → GitHub push → IndexNow ping
- **`/admin/*`** — password-protected dashboard: view users/conversations, pause/resume, send messages

### Data Model
- **`users`** — `phone` (PK), `guide`, `nb_echanges`, `etat`, `abonne`, `stripe_customer_id`, `depuis_site` (activation-code flow), ritual tracking, relance (follow-up) state
- **`messages`** — `phone` (FK), `role` (user/assistant), `content`, `timestamp`

### Divination Content
Hard-coded in `auryel_bot.py`:
- `PSAUMES` dict — 150 psalms with spiritual interpretations
- `TIRAGES_TAROT` list — 52 Marseille tarot cards with meanings
- `CHIFFRES_SAGESSE` dict — numbers 0–10 with numerology wisdom

### Guide System
5 guides with distinct personalities (system prompt varies per guide). Activation codes allow site visitors to unlock 6 additional premium guides (Séléna, Luna, Théa, Orion, Kaël, Raphaël). The `depuis_site` flag routes these users differently.

### Engagement Automation
- Intimacy levels escalate based on `nb_echanges`: strangers → acquaintances → close friends
- Conversion triggers fire randomly (35% chance) after 3+ exchanges
- Cron at `/cron/daily` handles day-based lifecycle (Days 6/7/8) and subscriber re-engagement

## Static Site (Netlify)

40 HTML landing pages in the repo root. They share a consistent structure:
- Dark theme + gold accents via CSS variables
- Mobile-first responsive layout
- Schema.org JSON-LD for SEO
- CTA buttons linking to WhatsApp or Stripe checkout

The SEO cron job generates new articles as HTML and commits them to the repo, triggering a Netlify redeploy.

## Règles critiques

1. **Ne jamais modifier `update_user_silent()`** — cette fonction effectue les mises à jour internes sans écraser `date_dernier_contact`. La modifier casserait le tracking de la dernière interaction utilisateur.
2. **Mapping des états Stripe** :
   - `active` / `trialing` → `abonne=True`, `etat=normal`
   - `past_due` / `unpaid` / `canceled` → `abonne=False`, `etat=pause`
3. **Le fichier principal est `auryel_bot.py`** — ne pas confondre avec d'éventuels fichiers versionés locaux (`auryel_bot_v8.py`, etc.).
4. **PORT Railway = 8080** — Railway expose toujours le port 8080 ; ne pas le changer dans le `Procfile` ou la config.
5. **Ne jamais créer `real_engine.py`** — ce fichier appartient à un autre projet (bot Solana) et n'a aucune place ici.
6. **Pushs SEO via `GITHUB_TOKEN`** — le cron `/cron/seo-publish` pousse les articles générés vers GitHub en utilisant exclusivement `GITHUB_TOKEN` (var d'env Railway). Ne pas substituer d'autre mécanisme d'authentification Git.

## API Routes Summary

| Route | Purpose |
|-------|---------|
| `GET /webhook` | WhatsApp verification handshake |
| `POST /webhook` | Inbound message handler |
| `POST /stripe/webhook` | Stripe payment events |
| `POST /stripe/create-checkout` | Generate Stripe checkout link |
| `GET/POST /cron/daily?secret=` | Daily lifecycle cron |
| `GET/POST /cron/seo-publish?secret=` | SEO article generation |
| `GET/POST /admin/login` | Admin auth |
| `GET /admin` | Admin dashboard |
| `POST /admin/pause` / `/admin/resume` | User account control |
| `POST /admin/send` | Send custom WhatsApp message |
| `GET /health` | Structured health check |
