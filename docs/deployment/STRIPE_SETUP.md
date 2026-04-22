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

### Where LLM cost values come from (`llm_usage.total_cost`)

The `usage_cost` meter sums dollar-denominated `llm_usage.total_cost` across the billing period, converts the delta since the last report to microdollars (1 microdollar = $0.000001), and pushes that integer value to Stripe — which aggregates meter events with SUM to produce the period total. The DB stores dollars as `numeric(16,9)`; the reporter multiplies by 1,000,000 and rounds up (`math.ceil`) before pushing, so sub-microdollar precision loss never causes under-billing. Each row's `total_cost` is resolved by a two-tier hierarchy, in order:

1. **Provider-reported cost (authoritative).** If the upstream provider returns `usage.cost` on the wire (OpenRouter does; OpenAI direct does not), the value is recorded verbatim. Under this path `llm_usage.input_cost` and `llm_usage.output_cost` are both `0` — the provider returns a single total, not a split.
2. **DB-rate fallback.** If the caller passed `total_cost = Decimal(0)` (the sentinel for "no wire-reported cost"), cost is computed as `input_tokens × cost_per_input_unit + output_tokens × cost_per_output_unit` from the `llm_models` row. These rates are synced at application startup from [`backend/src/shu/core/model_pricing.py`](../../backend/src/shu/core/model_pricing.py), which is the editable source of truth. Under this path `input_cost + output_cost == total_cost`.
3. **No rates, no wire cost.** A local/self-hosted model with no `model_pricing.py` entry records `0` for all three cost columns — appropriate because no external billing occurred.

To reprice a model, edit `model_pricing.py` and restart. To cover a model whose provider doesn't return `cost`, add it to the same file. `llm_models.cost_per_input_unit` and `cost_per_output_unit` carry per-token rates for `chat`/`embedding` models and per-page rates for `ocr` models; `llm_models.model_type` disambiguates the unit.

### Per-user attribution (`llm_usage.user_id`)

Every `llm_usage` row whose originating user is identifiable populates `user_id`: chat from `conversation_owner_id`, side-call and ingestion (OCR + embedding) from the user who initiated the job. This is the basis for future per-user invoicing and per-user quota enforcement. Rows with `user_id IS NULL` indicate genuinely user-less surfaces (currently: side-calls emitted during document profiling — a known limitation flagged for follow-up).

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
SHU_STRIPE_SECRET_KEY="sk_test_..."  # pragma: allowlist secret
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
# Tenant identifiers — obtained from the Stripe Dashboard after the customer's
# subscription is created externally (e.g., via the onboarding portal).
# These are seeded into billing_state on first boot so webhook handlers and
# scheduler jobs work immediately.
SHU_STRIPE_CUSTOMER_ID="cus_..."
SHU_STRIPE_SUBSCRIPTION_ID="sub_..."

# Base URL for Customer Portal return redirect
# This is the customer-facing URL of THEIR Shu instance
SHU_APP_BASE_URL="https://acme.shu.example.com"

# Webhook signing secret — UNIQUE PER WEBHOOK ENDPOINT
# Local dev: value from `stripe listen`
# Deployed: value from the Dashboard webhook endpoint you create in 2.4
SHU_STRIPE_WEBHOOK_SECRET="whsec_..."  # pragma: allowlist secret
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
- **API version**: `2025-05-28.basil` (pinned by stripe SDK 12.2.0 — update when upgrading)
- **Events to send** (select exactly these — handlers exist for all of them):
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

Expected after startup with `SHU_STRIPE_CUSTOMER_ID` and `SHU_STRIPE_SUBSCRIPTION_ID` set:

```json
{
  "data": {
    "stripe_customer_id": "cus_...",
    "stripe_subscription_id": "sub_...",
    "subscription_status": "pending",
    "user_count": 1,
    "user_limit": 0,
    "user_limit_enforcement": "soft",
    "at_user_limit": false,
    "cancel_at_period_end": false
  }
}
```

`stripe_customer_id` and `stripe_subscription_id` are populated from the env vars at startup.
`subscription_status` stays `"pending"` until the first `customer.subscription.created` webhook
arrives from Stripe.

### 3.3 Env seeding verification

Confirm that startup seeding wrote the tenant identifiers correctly.

1. Set `SHU_STRIPE_CUSTOMER_ID` and `SHU_STRIPE_SUBSCRIPTION_ID` in the instance env.
2. Start (or restart) the instance.
3. Check the startup logs for:

   ```text
   Seeded billing_state from env config {"fields": ["stripe_customer_id", "stripe_subscription_id"]}
   ```

   If this line is absent, either the env vars were not set or the row was already populated
   (normal on restart — seeding only writes NULL fields).
4. Call `/api/v1/billing/subscription` (admin JWT). Confirm `stripe_customer_id` and
   `stripe_subscription_id` match the values from the env vars.
5. Trigger a test webhook from the Stripe Dashboard (or Stripe CLI) to confirm the customer
   scoping guard accepts events for this customer ID and drops events for others.

### 3.4 Quantity sync

Create a second user via `/api/v1/auth/users`. Within a few seconds, server logs should show:

```text
Quantity sync completed { subscription_id: "sub_...", user_count: 2 }
```

Verify in the Stripe Dashboard: **Customers > [Your customer] > Subscriptions > [Subscription] > Quantity** shows `2`.

### 3.5 Usage reporting

Usage reporting runs on the scheduler's hourly interval. For testing, you can either:

**Option A: Wait for the schedule.** Generate some LLM activity (chat, profiling), wait up to an hour, then check the Stripe Dashboard: **Customers > [Your customer] > Billing meters**. The `usage_cost` meter should show a non-zero total.

**Option B: Force a run.** Restart the backend — the scheduler runs sources on startup after their
interval elapses. Or reset `last_reported_total` and `last_reported_period_start` in `billing_state`
(via `psql -d shu -c "UPDATE billing_state SET last_reported_total=0, last_reported_period_start=NULL WHERE id=1;"`)
to force a fresh reconciliation on next tick.

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

## Shu-managed vs BYOK Providers

Shu distinguishes between **Shu-managed** LLM providers (whose API usage is billed through this Stripe account) and **BYOK** providers (bring-your-own-key, where the customer pays the upstream provider directly). This section documents how that distinction is enforced and how operators override it.

### The `is_system_managed` column

`llm_providers.is_system_managed` (boolean, default `FALSE`) marks a provider row as Shu-managed. When `TRUE`, the provider is considered part of the hosted offering — its usage flows through the `usage_cost` meter and is billed to the customer via Stripe.

- The flag is **server-assigned only**. It is absent from every create/update request schema on the admin API, so customer admins cannot set, clear, or observe it as a writable field.
- New rows created via `POST /api/v1/llm/providers/` always get `is_system_managed=FALSE`.
- The flag is **immutable through the customer admin API**. It can only be changed via the operational-override path below.

### Lockdown semantics

When `is_system_managed=TRUE`, the customer admin API locks down mutation of the provider and its child models:

**Provider endpoints (`/api/v1/llm/providers/{id}`):**

- `PUT` and `DELETE` return HTTP 403 with detail `"Provider is managed by Shu and cannot be modified."`
- `POST .../sync-models`, `POST .../test`, `POST .../models` (create a new model under the provider), and all `GET` read endpoints remain allowed — operators still need to add models, refresh the catalog, and inspect health.

**Model endpoints (`/api/v1/llm/models/{id}`):**

- `DELETE` on a model whose parent provider is system-managed returns HTTP 403 with detail `"Model is managed by Shu and cannot be modified."`

### Operational-override path

Changes to `is_system_managed` (flipping a BYOK provider to Shu-managed or vice versa) happen outside the customer admin API:

- **Direct database access**: `UPDATE llm_providers SET is_system_managed = TRUE WHERE id = ...;`
- **HMAC-signed control-plane calls** from the hosted-offering control plane (SHU-697).

Either way, this override **does not go through the customer admin API** — customers (including customer admins) cannot promote or demote providers themselves.

> **⚠️ Never flip `is_system_managed` on a provider that already has usage history.**
>
> Billing aggregation (`usage_cost` meter) joins `llm_usage` to `llm_providers` and filters on the *current* value of `is_system_managed`. The flag is **not** snapshotted per-usage-row. Consequences:
>
> - **FALSE → TRUE** retroactively pulls every past BYOK-era usage row into the customer's next Stripe invoice. The customer already paid the upstream provider directly for those tokens, and they would be double-billed for work that was never Shu's to bill.
> - **TRUE → FALSE** erases previously-billable usage from future aggregation windows. If the flip happens mid-period before the invoice closes, Shu loses revenue for tokens already served under the hosted offering.
>
> Only flip this flag on providers with **zero** rows in `llm_usage`. For a provider that already has traffic, create a new provider row at the correct provenance and migrate routing instead — do not mutate the existing one. A future migration may add `llm_usage.billed_to_shu` to snapshot provenance at insert time and remove this constraint; until then, treat the flag as a one-way decision made at provider creation.

### `SHU_LOCK_PROVIDER_CREATIONS`

An instance-level kill switch for new provider creation:

```bash
# Default: FALSE — provider creation allowed
SHU_LOCK_PROVIDER_CREATIONS=FALSE
```

When set to `TRUE`:

- `POST /api/v1/llm/providers/` returns HTTP 403 with detail `"Provider creation is disabled on this deployment."`
- All other endpoints (`PUT`, `DELETE`, sync-models, `POST /models`, `GET`) continue to function, subject to the system-managed rules above. The lock is **create-only**.
- `GET /api/v1/config/public` exposes the flag as `lock_provider_creations` so the frontend hides the Add Provider button.

Typical use: hosted deployments where the set of providers is curated by Shu and customers should not add their own.

---

## Troubleshooting

### "Billing is not configured" (503 on billing endpoints)

Cause: `SHU_STRIPE_SECRET_KEY` is missing or empty. All billing endpoints refuse to operate when
the secret key is absent.

### Seeding didn't populate stripe_customer_id / stripe_subscription_id

Causes to check in order:

1. Are `SHU_STRIPE_CUSTOMER_ID` and `SHU_STRIPE_SUBSCRIPTION_ID` set in the instance env? Check
   with `printenv | grep SHU_STRIPE_CUSTOMER`.
2. Was the row already populated from a previous boot? Seeding only writes NULL fields — it will
   not overwrite existing values. Inspect with
   `psql -d shu -c "SELECT stripe_customer_id, stripe_subscription_id FROM billing_state WHERE id=1;"`.
3. Did startup log `"billing_state init failed"`? An exception during seeding is swallowed with a
   warning. Check the full log for the error details.

### Webhook signature verification fails

Cause: `SHU_STRIPE_WEBHOOK_SECRET` doesn't match the endpoint. For local dev, the `stripe listen`
command prints a fresh secret each time — copy that to `.env` and restart. For deployed instances,
the secret is per-endpoint; copy it from the Dashboard webhook page.

### Webhook logged as "Ignoring webhook — SHU_STRIPE_CUSTOMER_ID not configured"

Cause: `billing_state.stripe_customer_id` is NULL. Set `SHU_STRIPE_CUSTOMER_ID` and restart so
seeding populates the field. All webhooks are dropped until the instance has a known customer ID.

### Webhook logged as "Ignoring webhook for different customer"

Cause: The event is for a different Stripe customer. Multi-instance safety working correctly —
all instances on the same Stripe account receive all events; each instance only processes its own.
No action needed.

### Usage meter shows 0 after hours of activity

Causes to check in order:

1. Is `SHU_STRIPE_METER_ID_COST` set? If blank, reporting is skipped.
2. Is `billing_state.stripe_customer_id` populated? Without it, reporting is skipped.
3. Does `llm_usage.total_cost` show non-zero values for the period? If zero, no cost was recorded
   upstream.
4. Check server logs for `"Usage reporting failed"` — may indicate Stripe API issues or malformed
   meter configuration.

### Quantity out of sync with actual user count

The daily scheduler job reconciles any drift. For immediate sync, restart the backend — the
scheduler source will run on the next tick. If quantity still diverges, check Stripe API key
permissions (the key must have write access to subscriptions).

### Subscription stays "pending" after instance setup

Causes:

1. `SHU_STRIPE_CUSTOMER_ID` / `SHU_STRIPE_SUBSCRIPTION_ID` not set — without these Shu ignores
   all webhooks. Set them and restart.
2. Webhook endpoint not receiving events — verify the endpoint URL is reachable from Stripe and
   appears in **Developers > Webhooks** with recent successful deliveries.
3. Webhook secret mismatch — signature verification failures log a warning; check logs.
