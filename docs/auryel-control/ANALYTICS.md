# Auryel Control — first-party analytics

This document describes durable first-party telemetry for Auryel Control.
Analytics is observation only: billing remains authoritative for entitlements
and time credits, and analytics never grants access or time.

## Privacy contract

The writer accepts only allowlisted events and bounded properties. It rejects
conversation text, message content, AI prompts/responses, passwords, auth
tokens, FCM/APNs tokens, API keys and signed media URLs. Session identifiers
and billing transaction identifiers are HMAC-hashed before storage.

`analytics_events` is append-only from the application point of view. An
`idempotency_key` makes repeated deliveries harmless. Properties are limited
to 20 keys, 160 characters per string and 2048 bytes total.

## Event contract

| Event | Trigger/source | Properties | Use |
|---|---|---|---|
| `app_opened`, `session_started` | Authenticated Flutter session | none | Activity/cohorts |
| `account_created` | Successful server registration | none | Funnel cohort base |
| `onboarding_completed` | Flutter onboarding completion | none | Funnel |
| `free_consultation_started`, `free_consultation_used`, `paywall_viewed` | Product lifecycle contract | bounded metadata only | Funnel |
| `premium_purchased` | Verified billing result | store/product metadata | Funnel/billing |
| `consultation_started`, `consultation_ended`, `consultation_interrupted` | Consultation lifecycle | advisor, credit source, duration | Usage |
| `content_opened`, `content_started`, `content_completed` | Content lifecycle | content type/id, duration | Content usage |
| `wake_scheduled`, `wake_triggered`, `wake_snoozed`, `wake_stopped` | Wake lifecycle | non-sensitive metadata | Wake usage |
| `notification_opened` | Notification click | category/platform | Notification open rate |
| `premium_renewal`, `premium_expired`, `premium_refunded`, `premium_revoked` | Verified billing lifecycle | store/product metadata | Billing |
| `extra_hour_purchase`, `extra_hour_credited`, `extra_hour_refunded` | Verified consumable lifecycle | store/product metadata | Billing |

`POST /api/app/analytics/events` requires a normal mobile session; it is not
an anonymous collector. The full allowlist and privacy guard are enforced in
`analytics_tracking.py`.

Current producer coverage is intentionally conservative: `consultation_started`
and `consultation_interrupted` are emitted where observable, while
`consultation_ended` remains pending because the current consultation flow has
no single reliable user-visible close event independent of timer/ledger
consumption. Content open/start/completion is emitted only at lifecycle points
that the client can observe; unsupported reader completion is not fabricated.

## Durable ledgers

- `billing_events` stores verified store facts. Amount and currency remain
  NULL when the verifier does not provide them; catalogue prices are not
  invented.
- `ai_usage` stores provider/model, token counts, latency and status only.
  Prompt and response content are never stored. Cost remains NULL with
  `cost_status=unconfigured` until matching `ai_pricing_config` exists.
- `acquisition_attribution` stores optional first-party attribution. Missing
  attribution remains `NULL`/`unknown`.

## Retention definition

An account created on calendar date J is retained on D1, D7 or D30 only when
an `app_opened` or `session_started` event exists on the exact Europe/Paris
calendar date J+N. Results are available only from first-party instrumentation onward;
earlier history is not represented as zero.

## Fail-safe behaviour

The Flutter client uses a short timeout, no retry loop and no user-visible
error. A failed analytics request cannot block authentication, consultation,
billing, notifications, content or Wake.
