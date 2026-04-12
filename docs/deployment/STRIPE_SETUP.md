# Stripe Setup Guide

This guide walks through configuring Stripe and each Shu instance for billing. It covers two sides:

1. **Stripe Dashboard configuration** — done once per Stripe account (shared across all Shu instances)
2. **Shu instance configuration** — done once per customer instance

## Architecture Assumptions

- One Stripe account serves the hosted offering
- Each customer gets their own Shu instance with its own database and webhook endpoint URL
- Per-seat pricing: flat $222/user/month (licensed price)
- Overage pricing: actual LLM cost + margin, reported in microdollars (metered price)
- Margin is applied via the Stripe price, not in application code

Architecture reference: [stripe-architecture.mermaid](../diagrams/stripe-architecture.mermaid)

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Part 1: Stripe Dashboard Setup (Once)](#part-1-stripe-dashboard-setup-once)
3. [Part 2: Shu Instance Configuration (Per Customer)](#part-2-shu-instance-configuration-per-customer)
4. [Part 3: Verification](#part-3-verification)
5. [Going Live](#going-live)
6. [Troubleshooting](#troubleshooting)

---

## Prerequisites

- A Stripe account. Start in **test mode** for development and UAT.
- Admin access to the Stripe Dashboard
- Stripe CLI installed for local webhook testing: `brew install stripe/stripe-cli/stripe`
- A Shu instance (Docker Compose or K8s) that will serve one customer

---

## Part 1: Stripe Dashboard Setup (Once)

Do this once per Stripe account. The resulting identifiers (product, prices, meter) are shared across all Shu instances that bill through this account.

### 1.1 Get API keys

**Dashboard > Developers > API keys**

Record:

- **Secret key** (`sk_test_…` or `sk_live_…`)
- **Publishable key** (`pk_test_…` or `pk_live_…`)

The secret key is used by the Shu backend; the publishable key is exposed to the frontend.

### 1.2 Create the product

**Dashboard > Product catalog > Add product**

- **Name**: `Shu Pro` (or your branded name — shows on customer invoices)
- **Description**: Optional

Record the **Product ID** (`prod_…`).

### 1.3 Create the per-seat (licensed) price

On the product page, **Add another price**:

- **Pricing model**: Standard pricing
- **Price**: `$222.00` per unit
- **Billing period**: Monthly
- **Usage type**: Licensed (quantity is set by Shu via the API — not metered)

Record the **Price ID** (`price_…`). This is the `SHU_STRIPE_PRICE_ID_MONTHLY`.

### 1.4 Create the cost meter

**Dashboard > Billing > Meters > Create meter**

- **Display name**: `Usage Cost`
- **Event name**: `usage_cost` (must match `SHU_STRIPE_METER_EVENT_NAME`)
- **Aggregation formula**: `Sum`
- **Event payload key for value**: `value`
- **Customer mapping**: Use payload key `stripe_customer_id`

Record the **Meter ID** (`mtr_…`). This is the `SHU_STRIPE_METER_ID_COST`.

### 1.5 Create the metered (overage) price

Back on the product page, **Add another price**:

- **Pricing model**: Usage-based, linked to the `usage_cost` meter
- **Price per unit**: This is where margin is applied. Shu reports cost in **microdollars** (1 microdollar = $0.000001).
  - **Pass-through (no margin)**: `$0.000001` per unit (or expressed as `$1.00 per 1,000,000 units` if the UI allows)
  - **20% margin**: `$0.0000012` per unit
  - **30% margin**: `$0.0000013` per unit
  - **Custom per-customer margin**: Create separate metered prices and attach the right one to each customer's subscription
- **Billing period**: Monthly

No environment variable is needed for this price — Stripe applies it automatically to subscriptions that include it.

### 1.6 Confirm both prices are attached to the product

The product should now show two prices: the $222/seat licensed price and the usage-based metered price. Both will be included on each subscription so Stripe invoices the licensed fee plus metered overage.

### 1.7 Choose webhook strategy

For **local development**, use the Stripe CLI:

```bash
stripe login
stripe listen --forward-to http://localhost:8000/api/v1/billing/webhooks
```

The CLI prints a webhook signing secret (`whsec_…`) each session. Use this for local `SHU_STRIPE_WEBHOOK_SECRET`.

For **deployed customer instances**, create a webhook endpoint per instance in the Dashboard (see section 2.4 below). Each deployed instance has a unique URL and a unique webhook secret.

### 1.8 Test cards

Use these in test mode:

| Card | Behavior |
|------|----------|
| `4242 4242 4242 4242` | Successful payment |
| `4000 0000 0000 9995` | Declined (insufficient funds) |
| `4000 0025 0000 3155` | Requires 3D Secure authentication |

Any future expiry, any CVC, any ZIP.

---

## Part 2: Shu Instance Configuration (Per Customer)

Do this for each customer instance. Set these environment variables in the instance's `.env` file, Docker Compose environment, or K8s Secret/ConfigMap.

### 2.1 Variables that are the same across all instances

These come from Stripe Dashboard Part 1 and are identical for every customer billed through the same Stripe account:

```bash
# Stripe API keys (from Part 1.1)
SHU_STRIPE_SECRET_KEY="sk_test_..."
SHU_STRIPE_PUBLISHABLE_KEY="pk_test_..."

# Product and per-seat price (from Parts 1.2 and 1.3)
SHU_STRIPE_PRODUCT_ID="prod_..."
SHU_STRIPE_PRICE_ID_MONTHLY="price_..."

# Cost meter (from Part 1.4)
SHU_STRIPE_METER_ID_COST="mtr_..."
SHU_STRIPE_METER_EVENT_NAME="usage_cost"

# Mode must match the key prefix (validated at startup)
SHU_STRIPE_MODE="test"  # or "live"
```

### 2.2 Variables that differ per instance

```bash
# Base URL for Stripe redirects (success/cancel after Checkout, portal return)
# This is the customer-facing URL of THEIR Shu instance
SHU_APP_BASE_URL="https://acme.shu.example.com"

# Webhook signing secret — UNIQUE PER WEBHOOK ENDPOINT
# Local dev: value from `stripe listen`
# Deployed: value from the Dashboard webhook endpoint you create in 2.4
SHU_STRIPE_WEBHOOK_SECRET="whsec_..."
```

### 2.3 Optional / operational variables

```bash
# How often to push usage deltas to Stripe (default: 3600 = hourly)
SHU_STRIPE_USAGE_REPORT_INTERVAL=3600

# Grace period before suspending after payment failure (default: 7 days)
SHU_STRIPE_PAYMENT_GRACE_DAYS=7

# Informational only — actual credits are configured in the Stripe product
SHU_STRIPE_INCLUDED_TOKENS_PER_USER=0
```

### 2.4 Register the per-instance webhook endpoint

**Dashboard > Developers > Webhooks > Add endpoint**

- **Endpoint URL**: `https://<customer-instance-domain>/api/v1/billing/webhooks`
- **API version**: Latest
- **Events to send** (select exactly these — handlers exist for all of them):
  - `checkout.session.completed`
  - `customer.created`
  - `customer.subscription.created`
  - `customer.subscription.updated`
  - `customer.subscription.deleted`
  - `invoice.paid`
  - `invoice.payment_failed`

After creation, click the endpoint to reveal its **Signing secret** (`whsec_…`). Set this as `SHU_STRIPE_WEBHOOK_SECRET` on that specific customer's instance.

### 2.5 Start the instance

```bash
make up-full-dev    # local dev
# or your production deployment command
```

On startup, Shu validates the configuration. Check the logs for any "SHU_STRIPE_*" warnings.

---

## Part 3: Verification

Run these checks after configuring a new instance. All URLs use `http://localhost:8000` for local dev — substitute the instance's public URL for deployed instances.

### 3.1 Config endpoint (no auth required)

```bash
curl http://localhost:8000/api/v1/billing/config
```

Expected:

```json
{
  "data": {
    "configured": true,
    "publishable_key": "pk_test_...",
    "mode": "test"
  }
}
```

If `configured` is `false`, the backend didn't read the env vars — check for typos and restart.

### 3.2 Subscription status (admin auth required)

```bash
curl -H "Authorization: Bearer <admin-jwt>" http://localhost:8000/api/v1/billing/subscription
```

Expected for a fresh instance:

```json
{
  "data": {
    "stripe_customer_id": null,
    "stripe_subscription_id": null,
    "subscription_status": "pending",
    "user_count": 1,
    "user_limit": 0,
    "user_limit_enforcement": "soft",
    "at_user_limit": false,
    "cancel_at_period_end": false
  }
}
```

### 3.3 Checkout flow end-to-end

1. As the instance admin, POST to `/api/v1/billing/checkout` with `{"quantity": 1}`. You'll get back a Stripe Checkout URL.
2. Open the URL in a browser. Pay with `4242 4242 4242 4242`.
3. After redirect to success URL, the server logs should show (in order):
   - `Received webhook { event_type: "customer.created" }` → **ignored** (instance not yet linked; this is correct)
   - `Received webhook { event_type: "checkout.session.completed" }` → processed (session ID matches the pending claim from checkout creation)
   - `Received webhook { event_type: "customer.subscription.created" }` → processed
4. Call `/api/v1/billing/subscription` again. Now `stripe_customer_id`, `stripe_subscription_id`, and `subscription_status: "active"` should be populated.

If the `checkout.session.completed` event is logged as ignored, it means either:

- Stripe delivered it before the local upsert of `pending_checkout_session_id` finished (shouldn't happen in practice — the upsert completes before returning the URL)
- The customer completed a checkout session that wasn't initiated by this instance (multi-instance safety: working as designed)

### 3.4 Quantity sync

Create a second user via `/api/v1/auth/users`. Within a few seconds, server logs should show:

```
Quantity sync completed { subscription_id: "sub_...", user_count: 2 }
```

Verify in the Stripe Dashboard: **Customers > [Your customer] > Subscriptions > [Subscription] > Quantity** shows `2`.

### 3.5 Usage reporting

Usage reporting runs on the scheduler's hourly interval. For testing, you can either:

**Option A: Wait for the schedule.** Generate some LLM activity (chat, profiling), wait up to an hour, then check the Stripe Dashboard: **Customers > [Your customer] > Billing meters**. The `usage_cost` meter should show a non-zero total.

**Option B: Force a run.** Restart the backend — the scheduler runs sources on startup after their interval elapses. Or reach into `system_settings` and unset `pending_checkout_session_id` and any `last_reported_*` keys to force a fresh reconciliation on next tick.

### 3.6 Customer portal

```bash
curl -H "Authorization: Bearer <admin-jwt>" http://localhost:8000/api/v1/billing/portal
```

Returns a Stripe Customer Portal URL. Opening it lets the customer update payment methods, view invoices, and cancel.

---

## Going Live

When switching from test to live mode:

1. Create live-mode equivalents of everything in Part 1 (product, prices, meter, webhook endpoint)
2. Update `.env` on each customer instance:
   - `SHU_STRIPE_SECRET_KEY` to `sk_live_…`
   - `SHU_STRIPE_PUBLISHABLE_KEY` to `pk_live_…`
   - `SHU_STRIPE_WEBHOOK_SECRET` to the live webhook endpoint's secret
   - `SHU_STRIPE_PRODUCT_ID`, `SHU_STRIPE_PRICE_ID_MONTHLY`, `SHU_STRIPE_METER_ID_COST` to the live-mode IDs
   - `SHU_STRIPE_MODE="live"`
3. Restart the instance

Shu validates the key prefix against `SHU_STRIPE_MODE` at startup — a live key with `mode=test` (or vice versa) will raise a configuration error.

---

## Troubleshooting

### "Billing is not configured" (503 response on /billing/checkout)

Cause: `SHU_STRIPE_SECRET_KEY` is missing or empty. The endpoint refuses to return when billing isn't set up.

### Webhook signature verification fails

Cause: `SHU_STRIPE_WEBHOOK_SECRET` doesn't match the endpoint. For local dev, the `stripe listen` command prints a fresh secret each time — copy that to `.env` and restart. For deployed instances, the secret is per-endpoint; copy it from the Dashboard webhook page.

### `checkout.session.completed` logged as "no matching pending session"

Cause: The webhook is for a different instance's checkout. This is the multi-instance safety check working correctly — another tenant on the same Stripe account completed their checkout and Stripe fan-outs the event to all webhook endpoints. No action needed.

### Usage meter shows 0 after hours of activity

Causes to check in order:

1. Is `SHU_STRIPE_METER_ID_COST` set? If blank, reporting is skipped.
2. Is there a `stripe_customer_id` in `system_settings[billing]`? Without it, reporting is skipped.
3. Does `llm_usage.total_cost` show non-zero values for the period? If zero, no cost was recorded upstream.
4. Check server logs for `"Usage reporting failed"` — may indicate Stripe API issues or malformed meter configuration.

### Quantity out of sync with actual user count

The hourly scheduler job reconciles any drift. For immediate sync, restart the backend — the scheduler source will run on the next tick. If quantity still diverges, check Stripe API key permissions (the key must have write access to subscriptions).

### Subscription stays "pending" after checkout

Causes:

1. Webhook endpoint not receiving events — verify the endpoint URL is reachable from Stripe and appears in **Developers > Webhooks** with recent successful deliveries.
2. Webhook secret mismatch — signature verification failures log a warning; check logs.
3. Instance-binding rejected the event — if another tenant's checkout webhook arrived first, only the checkout our instance initiated will be accepted. Try the checkout flow again from the Shu admin UI.
