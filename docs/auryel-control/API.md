# Auryel Control — Admin API (local V1)

The private API is mounted under `/api/admin/*`. It is separate from mobile
authentication and accepts only an opaque bearer session returned by the admin
login endpoint:

```http
Authorization: Bearer <session_token>
```

Sessions expire after four hours and are revocable. Tokens are never stored
in clear text. No cookie authentication is enabled, so browser CSRF does not
apply; a future cookie mode must add CSRF protection before activation.

## Authentication

Password authentication is fail-closed for MFA. When an administrator has
not enrolled MFA yet, a successful password login returns a short-lived
opaque bearer session with `session_scope: "mfa_bootstrap"`. That session is
accepted only by `/api/admin/auth/me`, `/api/admin/auth/logout`, and the MFA
enrollment/confirmation endpoints. It cannot access dashboard data routes.

After a valid TOTP confirmation, the current session is promoted to
`session_scope: "full"` and receives the normal four-hour lifetime. Existing
sessions for already-enrolled administrators remain full; any active session
belonging to a non-enrolled administrator is narrowed to the short bootstrap
scope by the additive migration.

- `POST /api/admin/auth/login` — JSON `{email,password,mfa_code?}`; generic 401
  on failure. When MFA is enabled, password-only returns `mfa_required` and no
  session; a valid TOTP or one unused recovery code is required for the final
  session.
- `POST /api/admin/auth/logout` — revokes the current session.
- `GET /api/admin/auth/me` — current admin and session mode.
- `POST /api/admin/auth/mfa/enroll` — authenticated setup; returns a
  one-time provisioning URI/secret until confirmation.
- `POST /api/admin/auth/mfa/confirm` — authenticated confirmation with the
  first TOTP code; returns recovery codes once.
- `POST /api/admin/auth/mfa/recovery-codes` — authenticated, factor-protected
  regeneration; returns replacement codes once.
- `POST /api/admin/auth/mfa/disable` — authenticated, factor-protected
  deactivation.

The first account is created locally with:

```bash
DATABASE_URL='…' python3 scripts/create_admin.py --email admin@example.com
```

The password is entered interactively and is never put in source control.
TOTP secrets are encrypted with a key derived from `SECRET_KEY`; recovery
codes are stored only as HMAC hashes and are single-use. The secret and
recovery codes are returned only during their respective enrollment or
regeneration response and are never written to audit metadata.

## Aggregate endpoints

All endpoints below require admin authentication and return aggregate or
strictly controlled account data. They never return password hashes, session
tokens, push tokens, API credentials, or `messages.content`.

- `GET /api/admin/overview` — today, 7d and 30d aggregate windows. Activity
  counts are explicitly `*_proxy` values derived from `app_sessions.last_used_at`.
- `GET /api/admin/users?page=1&per_page=25&search=&premium=true|false&platform=ios|android`
  — paginated, masked-email user summaries.
- `GET /api/admin/users/<id>` — aggregate user detail only.
- `GET /api/admin/billing/overview` — store split and
  `estimated_gross_revenue`; `financial_accuracy` is always
  `estimated_catalog_gross`, excluding taxes, commissions, refunds and net cash.
- `GET /api/admin/consultations/overview?window=today|7d|30d`
- `GET /api/admin/advisors?window=today|7d|30d`
- `GET /api/admin/stars/overview?window=today|7d|30d`
- `GET /api/admin/content/overview`
- `GET /api/admin/notifications/overview?window=today|7d|30d`
- `GET /api/admin/technical/overview`
- `GET /api/admin/ai/overview` — durable AI usage metadata when available;
  cost remains unavailable until model pricing is configured.

## First-party analytics

Authenticated mobile clients may submit bounded observation events to
`POST /api/app/analytics/events` with `event_name`, optional `platform`,
optional bounded `properties`, and optional `idempotency_key`.

Admin-only analytics endpoints:

- `GET /api/admin/analytics/activity?window=today|7d|30d`
- `GET /api/admin/analytics/funnel?window=today|7d|30d`
- `GET /api/admin/analytics/retention`
- `GET /api/admin/analytics/content?window=today|7d|30d`
- `GET /api/admin/analytics/wake?window=today|7d|30d`
- `GET /api/admin/analytics/acquisition`

These responses expose `tracked_since` and reliability/status fields so
untracked history is not presented as zero. AI costs remain unavailable until
an effective `ai_pricing_config` row exists.

## CORS and browser security

Admin CORS is allowlisted through `AURYEL_CONTROL_CORS_ORIGINS`. The
production value must be exactly `https://dashboard.auryelvoyance.com`; local
development may explicitly use `http://localhost:3000` in a local environment
only. Wildcard origins are not supported. The current bearer/sessionStorage
mode avoids cookie CSRF but remains exposed to a successful dashboard XSS;
production must ship the CSP/security headers and keep the dashboard free of
unsafe HTML sinks.

Failed authentication is `401`; invalid pagination values are normalized to
safe defaults. Admin logins and API actions are written to
`admin_audit_events` with bounded, non-sensitive metadata.
