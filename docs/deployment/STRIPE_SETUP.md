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
2. [Part 1: Stripe Dashboard Setup (Once per account)](#part-1-stripe-dashboard-setup-once)
3. [Part 2: Customer Subscription Setup (Per Customer)](#part-2-customer-subscription-setup-per-customer)
4. [Part 3: Shu Instance Configuration (Per Customer)](#part-3-shu-instance-configuration-per-customer)
5. [Part 4: Verification](#part-4-verification)
6. [Going Live](#going-live)
7. [Troubleshooting](#troubleshooting)

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

The metered price is not a separate entity — it's an additional price on the Shu Pro product, linked to the meter you just created in 1.4. The Stripe Dashboard UI does not make this obvious; step-by-step:

1. **Dashboard > Product catalog > Shu Pro** (the product where you added the $222/seat price in 1.3).
2. Click **Add another price** (or the `+` next to existing prices on the product page).
3. In the pricing form, locate the **Pricing model** selector. Available options include Flat rate, Package, Tiered, and **Usage-based** — pick **Usage-based**.
4. A **Meter** dropdown appears. Select **Usage Cost** (the meter from 1.4).
5. **Price per unit** — this is where margin is applied. Shu reports cost in **microdollars** (1 microdollar = $0.000001):
   - **Launch setting — 30% margin**: `$0.0000013` per unit (SHU-663 epic decision; this is the default for the hosted offering)
   - Other options for reference:
     - Pass-through (no margin): `$0.000001` per unit
     - 20% margin: `$0.0000012` per unit
     - Custom per-customer margin: create separate metered prices and attach the right one to each customer's subscription
   - **If the UI's main input is limited to 4 decimal places**, expand **More options** or **Advanced** and enter the full value there. Alternatively, express it as a per-package rate: `$1.30 per 1,000,000 units` for the 30% margin setting.
6. **Billing period**: Monthly. **Currency**: USD.
7. Save.

No environment variable is needed for this price — Stripe applies it automatically to any subscription that includes it. The metered price does not need to be recorded anywhere in Shu configuration.

**If the Pricing model dropdown does not show "Usage-based"**: the Meters / usage-based billing feature may not be enabled on the Stripe account. Check **Dashboard > Settings > Billing > Subscriptions and emails** (or equivalent) for a feature toggle. If the option is genuinely missing, contact Stripe support to enable it; Meters went GA in 2024 and should be available on most accounts by default.

### 1.6 Confirm both prices are attached to the product

After saving the metered price in 1.5, the Shu Pro product page should list **two prices**: the `$222.00 / seat / month` licensed price from 1.3, and the new usage-based price linked to the `usage_cost` meter. Both prices must be added to every customer's subscription so Stripe can invoice the licensed fee plus metered overage on the same invoice.

A common mistake at this stage: creating a subscription with only the seat price. Such subscriptions will never produce overage charges — the customer will be billed only for seats, with usage silently discarded. When creating the test subscription in 1.5 / Phase 1.5 (before deploying Shu), make sure to **add both prices** to the subscription's line items. This is easy to miss because the Dashboard's subscription-creation flow defaults to one price per subscription.

### 1.6.1 Subscription quantity rule: always start at 1

**When creating the subscription, set the licensed price's `quantity` to `1` — always, regardless of the customer's contracted seat count.**

Rationale: Shu's quantity-sync (SHU-677) treats the actual user count in `llm_usage` / the Shu DB as the source of truth and pushes it to Stripe on user create/delete plus a daily reconciliation. A fresh Shu instance has 0 users until the admin bootstraps, at which point the first user creation event fires a quantity sync that sets Stripe's `quantity` to 1. If you started the subscription at `quantity=5`:

- Stripe immediately charges the first invoice for 5 seats.
- Shu boots, syncs to `quantity=1`, Stripe prorates the 4-seat overcharge as a credit on the next invoice. Customer sees a confusing first invoice with large charges and proration lines.
- (Post-SHU-704) The initial Credit Grant, issued on `customer.subscription.created`, is sized at `$50 × 5 = $250` — four times the intended $50 allowance. Shu then drops quantity to 1, but the over-granted credits remain for the current period. Silent revenue leak.

Seats grow naturally as admins add users: `quantity=1` at subscription creation → admin adds their second user → Shu's real-time sync bumps Stripe `quantity` to 2 → Stripe prorates the new seat on the current invoice → and so on. Contractual seat minimums (if the product ever adopts them) are customer-facing policy, not a Stripe `quantity` value — they should live as subscription metadata or contract terms, not as an initial seat count on the subscription itself.

The metered price has no `quantity` field — usage from the meter drives billing regardless of seat count.

This rule applies to all subscription creation paths: manual creation in the Dashboard (this doc), the future customer onboarding portal (SHU-664), and any scripted/automated provisioning. The onboarding portal ticket should explicitly encode `quantity=1` in its subscription-creation call.

### 1.7 Enable Credit Grants for the per-seat included usage allowance

Each seat on the hosted offering includes **$50/user/month** of LLM + embedding + OCR usage (SHU-663 epic decision). This allowance is implemented via the Stripe Credit Grants API — programmatic credit issuance by Shu, scoped to the metered price, applied automatically on the customer's invoice before charging.

**Preconditions on the Stripe account**:

1. **Dashboard > Settings > Billing > Credits** — confirm Credit Grants are enabled on the account. If the page is missing or locked behind a feature flag, contact Stripe support to enable. Credit Grants went GA in 2024; most accounts should have access by default.
2. No Dashboard configuration of the allowance amount is required — once SHU-704 lands, Shu will issue the grant programmatically using the configured per-seat amount (`SHU_STRIPE_INCLUDED_USD_PER_USER`, default `50`) multiplied by `subscription.quantity`. Until then, the allowance is not applied; see the Implementation status note below.

**When grants are issued**:

- On `customer.subscription.created` webhook: initial grant for the current period.
- At each period rollover (driven by `invoice.paid` or the scheduler): new grant for the new period.
- On `customer.subscription.updated` with a quantity delta: grant is adjusted (exact proration semantics are an implementation detail — see the implementation ticket under SHU-663).

**Verification after creating a test subscription**: Dashboard > Customers > [your test customer] > scroll to **Credits** (or **Payments > Credits**) and confirm a grant appears with the expected amount (`$50 × quantity`). Confirm the grant's scope is restricted to the metered price, not applied to the seat fee.

**Implementation status**: Tracked under SHU-704. Until that ticket lands, test-mode invoices will show full usage charges with no credit applied. SHU-699 scenario #19 verifies end-to-end behavior once SHU-704 ships.

### 1.8 Choose webhook strategy

For **local development**, use the Stripe CLI:

```bash
stripe login
stripe listen --forward-to http://localhost:8000/api/v1/billing/webhooks
```

The CLI prints a webhook signing secret (`whsec_…`) each session. Use this for local `SHU_STRIPE_WEBHOOK_SECRET`.

For **deployed customer instances**, create a webhook endpoint per instance in the Dashboard (see section 3.4 below). Each deployed instance has a unique URL and a unique webhook secret.

### 1.9 Test cards

Use these in test mode:

| Card | Behavior |
|------|----------|
| `4242 4242 4242 4242` | Successful payment |
| `4000 0000 0000 9995` | Declined (insufficient funds) |
| `4000 0025 0000 3155` | Requires 3D Secure authentication |

Any future expiry, any CVC, any ZIP.

---

## Part 2: Customer Subscription Setup (Per Customer)

Done once per customer in the Stripe Dashboard, **before** configuring the Shu instance. Produces the `cus_…` and `sub_…` IDs that Part 3 wires into the Shu instance's environment. In production, the customer onboarding portal (SHU-664) automates these steps; this section documents the manual flow used for the lab and any out-of-band onboarding.

### 2.1 Create the customer

**Dashboard > Customers > Add customer**

- **Email**: contact email for the organization. Stripe sends invoices, payment-failure notices, and Portal links here.
- **Name**: organization name. Appears on invoices.
- Address, tax ID, metadata: optional; fill in if relevant for the customer's tax jurisdiction.

Save. Record the **Customer ID** (`cus_…`). This becomes `SHU_STRIPE_CUSTOMER_ID` on the customer's Shu instance (Part 3.2).

### 2.2 Attach a payment method

**On the customer page > Payment methods > Add payment method.**

For **test mode** (lab and UAT):

- **Card number**: `4242 4242 4242 4242` (success card from 1.9)
- **Expiry**: any future date (e.g. `12 / 30`)
- **CVC**: any 3 digits (e.g. `123`)
- **ZIP**: any (e.g. `12345`)

For **live mode**: do not add payment methods from the Dashboard. Customers self-onboard their own card via Stripe Checkout or the Customer Portal — the onboarding flow collects payment before creating the subscription.

**Why this matters before subscription creation**: without a default payment method, the subscription created in 2.3 lands in `status: incomplete` and never activates. Shu sees `subscription_status: pending` indefinitely, and SHU-703 enforcement (once it lands) blocks chat and ingestion. Easier to attach the card up front than to debug an incomplete subscription later.

### 2.3 Create the subscription with both prices

**On the customer page > Subscriptions > Add subscription.**

Critical configuration:

- **Customer**: pre-filled.
- **Pricing**: add **both** prices from Part 1. The Dashboard form defaults to one price; click **Add another item** (or `+`) to attach the second.
  - **Item 1**: the per-seat licensed price from 1.3 (`price_…`). **Quantity: 1** (see "Quantity rule" below — always 1 at creation, regardless of contracted seats).
  - **Item 2**: the metered usage-based price from 1.5 (linked to the `usage_cost` meter). No quantity field appears — usage from meter events drives billing.
- **Collection method**: Charge automatically (default). The default payment method from 2.2 is used.
- **Billing cycle anchor / trial**: leave defaults unless the customer's contract specifies otherwise.

Save. Record the **Subscription ID** (`sub_…`). This becomes `SHU_STRIPE_SUBSCRIPTION_ID` on the customer's Shu instance (Part 3.2).

**Quantity rule (critical, repeated from 1.6.1)**: subscription `quantity` must always be **1** at creation, regardless of any contracted seat count. Shu's quantity sync (SHU-677) treats actual user count as the source of truth and pushes Stripe down to 1 the moment the first admin user bootstraps. Starting at quantity > 1 produces:

- A first invoice charged for the higher seat count, then prorated credit on the next invoice when Shu syncs down. Confusing for the customer.
- (Once SHU-704 lands) An over-sized initial Credit Grant — quiet revenue leak until quantity sync corrects.

Seats grow naturally as admins add users: Shu pushes Stripe `quantity=2`, `=3`, etc. via real-time sync.

### 2.4 Verify subscription is active

On the customer page, confirm the subscription's **Status** is `active` (or `trialing` if you set a trial). If it shows `incomplete` or `incomplete_expired`, the most likely cause is a missing or invalid payment method — revisit 2.2. If it shows `past_due`, the test card was declined (use `4242 4242 4242 4242`, not the decline cards from 1.9).

### 2.5 (Optional) Pre-verify Credit Grants accessibility

Once SHU-704 ships, Shu issues a Credit Grant on the `customer.subscription.created` webhook. To pre-verify the Credit Grants feature is available on this customer, navigate to the customer page and look for a **Credits** tab (or **Payments > Credits**). The page should exist and be empty before SHU-704 lands. If the page is missing entirely, see Part 1.7's preconditions — the feature may not be enabled on the Stripe account.

### 2.6 Record what to pass to Shu

After 2.1–2.4, you should have:

| Value | Source | Used as |
|---|---|---|
| Customer ID (`cus_…`) | 2.1 | `SHU_STRIPE_CUSTOMER_ID` |
| Subscription ID (`sub_…`) | 2.3 | `SHU_STRIPE_SUBSCRIPTION_ID` |

These are the per-customer values that vary between Shu instances — every other Stripe identifier (product, prices, meter, publishable/secret keys) is shared across all instances on the same Stripe account and was already recorded in Part 1.

---

## Part 3: Shu Instance Configuration (Per Customer)

Do this for each customer instance. Set these environment variables in the instance's `.env` file, Docker Compose environment, or K8s Secret/ConfigMap.

### 3.1 Variables that are the same across all instances

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

### 3.2 Variables that differ per instance

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

### 3.3 Optional / operational variables

```bash
# How often to push usage deltas to Stripe (default: 3600 = hourly).
# The local-billing-lab overlay shortens this to 60 so meter pushes
# happen within a reasonable test window.
SHU_STRIPE_USAGE_REPORT_INTERVAL=3600

# Grace period applied only to the retryable `past_due` subscription
# status before blocking chat and ingestion. Terminal statuses
# (`unpaid`, `canceled`, etc.) block immediately regardless of this
# value. Default 0 (strict). See SHU-703.
SHU_STRIPE_PAYMENT_GRACE_DAYS=0

# Per-seat included usage allowance, in USD/month (launch default: 50,
# per SHU-663 epic decision). Shu uses this value to size the Stripe
# Credit Grant issued on subscription creation / period rollover
# (`grant_amount = SHU_STRIPE_INCLUDED_USD_PER_USER × subscription.quantity`).
# NOTE: replaces the previous informational-only
# `SHU_STRIPE_INCLUDED_TOKENS_PER_USER` variable.
SHU_STRIPE_INCLUDED_USD_PER_USER=50
```

### 3.4 Register the per-instance webhook endpoint

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

### 3.5 Start the instance

```bash
make up-full-dev    # local dev
# or your production deployment command
```

On startup, Shu validates the configuration. Check the logs for any "SHU_STRIPE_*" warnings.

---

## Part 4: Verification

Run these checks after configuring a new instance. All URLs use `http://localhost:8000` for local dev — substitute the instance's public URL for deployed instances.

### 3.1 Billing state seeded correctly (database — authoritative)

The direct source of truth is the `billing_state` row. Query it straight from Postgres:

```bash
psql -d shu -c "SELECT id, stripe_customer_id, stripe_subscription_id, quantity, subscription_status, current_period_start, current_period_end FROM billing_state;"
```

Expected after first boot:

```text
 id | stripe_customer_id |    stripe_subscription_id    | quantity | subscription_status | current_period_start | current_period_end
----+--------------------+------------------------------+----------+---------------------+----------------------+---------------------
  1 | cus_...            | sub_...                      |        0 | pending             |                      |
```

What to verify:

- A single row with `id=1` exists — the service-startup seed succeeded.
- `stripe_customer_id` and `stripe_subscription_id` match the values you set in env.
- `subscription_status` is `pending` — this is expected until the first `customer.subscription.created` webhook is delivered (Part 3.5).
- `current_period_*` are NULL until webhook delivery or scheduler reconciliation populates them.

If the row is missing or fields are NULL, the seed didn't run (usually because the service started before Postgres was ready — restart the service pod once Postgres is healthy). See `### Seeding didn't populate stripe_customer_id / stripe_subscription_id` under Troubleshooting.

### 3.1.1 Publishable key exposure (admin auth required)

Separate check: the frontend Stripe Elements flow needs the publishable key surfaced to admins. The `/billing/config` endpoint requires an admin JWT (all endpoints under `/api/v1/billing/*` are behind `AuthenticationMiddleware`; only `/api/v1/config/public` and explicit public paths bypass auth).

```bash
curl -H "Authorization: Bearer <admin-jwt>" http://localhost:8000/api/v1/billing/config
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

If `configured` is `false`, the env vars didn't load at startup — check for typos and restart. If you get `{"detail":"Authentication required"}` with no `Authorization` header, that's the expected middleware behavior, not a bug.

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

Shu applies an **asymmetric seat-change policy** (SHU-704): **seat increases take effect immediately**, **seat decreases are deferred to the next period boundary**. This closes the gaming vector where a customer buys N seats, burns the `N × $50` included-usage grant in a day, then drops back to 1 seat and pockets a proration refund. Upgrades are immediate because the customer is paying for and immediately using the additional allowance; downgrades wait because the customer has already paid for the current period's seat count and should keep that allowance through period end.

**Upgrade (add user) — verify immediate sync.** Create a second user via `/api/v1/auth/users`. Within a few seconds, server logs should show:

```text
Quantity sync completed { subscription_id: "sub_...", user_count: 2 }
```

Verify in the Stripe Dashboard: **Customers > [Your customer] > Subscriptions > [Subscription] > Quantity** shows `2`. Also verify a new Credit Grant appears on the customer sized `(added_seats × $50 × days_remaining/days_in_period)` — the additive delta grant. The original period grant is left intact.

**Downgrade (remove user) — verify deferral.** Deactivate or delete the second user. **No Stripe quantity change should occur this webhook cycle.** Instead:

- `billing_state.pending_quantity` is set to the new lower value (check via `psql -d shu -c "SELECT pending_quantity FROM billing_state WHERE id=1;"`).
- The current period's Credit Grant remains intact with its full balance.
- `GET /api/v1/billing/subscription` response now includes `pending_quantity` and `current_period_end` so a frontend can render "Downgrading to N seats effective <date>".

**Period rollover — verify deferred downgrade applies.** On the next `invoice.paid` webhook (simulate via Stripe CLI `trigger invoice.paid` or wait for the billing period), Shu calls `Subscription.modify(quantity=pending_quantity, proration_behavior="none")`, clears `pending_quantity` to NULL, then issues the new period's Credit Grant at the post-rollover quantity.

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
