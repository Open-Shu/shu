# Billing Customer Runbook

End-to-end procedure an operator follows to provision a billable customer (tenant) on the hosted Shu offering. Distinct from the UAT lab setup: see [BILLING_UAT_LAB_RUNBOOK.md](BILLING_UAT_LAB_RUNBOOK.md) for the docker-desktop UAT environment.

This runbook is **incomplete by design** — several sections depend on tickets that have not yet landed. Gaps are marked inline with the ticket they are waiting for, so you can tell whether a given section is ready to execute or blocked.

## Table of contents

1. [Purpose and scope](#purpose-and-scope)
2. [Tenant-provisioning overview](#tenant-provisioning-overview)
3. [Prerequisites](#prerequisites)
4. [Stripe account setup (one-time per Shu account)](#stripe-account-setup-one-time-per-shu-account)
5. [Per-customer Stripe subscription setup](#per-customer-stripe-subscription-setup)
6. [External provider configuration](#external-provider-configuration)
7. [Deployment overlay](#deployment-overlay)
8. [Secret provisioning](#secret-provisioning)
9. [Tenant registration with the control plane](#tenant-registration-with-the-control-plane)
10. [Apply, migrate, and seed](#apply-migrate-and-seed)
11. [First-webhook verification](#first-webhook-verification)
12. [Admin user setup](#admin-user-setup)
13. [Going live (test mode → live mode)](#going-live-test-mode--live-mode)
14. [Operational procedures](#operational-procedures)
15. [Tenant offboarding](#tenant-offboarding)

## Purpose and scope

This runbook walks an operator through the sequence of actions required to take a signed-up customer and produce a working, billed Shu instance. It covers:

- The Stripe-side setup that has to happen once per Shu account.
- The Stripe-side setup that has to happen per customer (customer + subscription creation).
- The Kubernetes-side setup that brings up the tenant's Shu instance and wires it to billing.
- The verification steps that confirm billing is working end-to-end before handing the instance to the customer.

Stripe **test mode** is the default throughout; see [Going live](#going-live-test-mode--live-mode) for the transition to live mode.

The hosted offering is a multi-tenant environment. "A tenant" or "a customer" refers to a single billable Shu instance; one customer → one Stripe customer → one subscription → one Shu instance (until multi-seat scaling changes that — see SHU-704 for the included-usage model and SHU-709 for the optional minimum-seat floor).

## Tenant-provisioning overview

High-level flow per new customer:

1. Customer signs up through whatever intake process exists.
2. Operator creates a Stripe customer + subscription in Stripe's dashboard (or via API).
3. Operator provisions the tenant's Shu instance on Kubernetes (overlay + secrets + config).
4. Operator registers the tenant with the Shu Control Plane (generates the HMAC shared secret, inserts the `tenant` row, syncs the secret to the tenant's shu-api).
5. Operator triggers a first webhook to confirm state transitions; verifies `/billing/config` and `/billing/subscription`.
6. Operator registers the customer's initial admin user and hands off credentials.

> **Gap — end-to-end tenant onboarding automation**
> Today this is a manual sequence. Automating it into a single operator command (or a control-plane API call) is SHU-696 / SHU-697 territory. Until that lands, the steps below are executed by hand in sequence.

## Prerequisites

Operator tooling:

- `kubectl` configured for the target hosted cluster (contexts for each environment — staging / production — should be explicit).
- `stripe` CLI for ad-hoc operations (customer creation, webhook registration). Not required in steady state but useful during provisioning.
- Access to the Shu account's Stripe dashboard (restricted-key level at minimum).
- Access to the hosted-offering DNS zone (for per-tenant subdomain provisioning).

Cluster-side requirements:

> **Gap — cluster shape**
> The target topology (one cluster serving many tenants vs. one cluster per tenant, namespace-per-tenant vs. row-level multi-tenancy, per-tenant Postgres vs. shared Postgres with per-tenant DBs) is still being decided. See SHU-667 (Multi-Tenant Infrastructure Setup). The density analysis in SHU-699 currently assumes shared cluster with per-tenant `shu-api` + `shu-frontend` pods, external Postgres, and shared Redis.

## Stripe account setup (one-time per Shu account)

Follow [STRIPE_SETUP.md Part 1](STRIPE_SETUP.md#part-1-stripe-dashboard-setup-once) end-to-end. This produces account-level resources shared across every customer:

| Output | Where it's used | Sensitivity |
|---|---|---|
| Restricted secret key `rk_test_...` / `rk_live_...` | `shu-secrets.stripe-secret-key` (per tenant) | Secret |
| Publishable key `pk_test_...` / `pk_live_...` | `configmap.SHU_STRIPE_PUBLISHABLE_KEY` (per tenant) | Public |
| Product ID `prod_...` | `configmap.SHU_STRIPE_PRODUCT_ID` (per tenant) | Public |
| Per-seat price ID `price_...` | `configmap.SHU_STRIPE_PRICE_ID_MONTHLY` (per tenant) | Public |
| Metered (overage) price ID `price_...` | Attached to subscription automatically; not directly in shu config | Public |
| Cost meter ID `mtr_..._..._...` | `configmap.SHU_STRIPE_METER_ID_COST` (per tenant) | Public |
| Cost meter event name | `configmap.SHU_STRIPE_METER_EVENT_NAME` (per tenant) | Public |

Do this once. Reuse the same values for every customer.

The price and meter are intentionally account-level, not per-customer — every tenant bills against the same per-seat price and pushes usage to the same meter, with Stripe accumulating per-customer totals internally.

## Per-customer Stripe subscription setup

Follow [STRIPE_SETUP.md Part 2](STRIPE_SETUP.md#part-2-customer-subscription-setup-per-customer) for each new customer. Outputs used below:

| Output | Where it's used |
|---|---|
| Customer ID `cus_...` | `shu-secrets.stripe-customer-id` |
| Subscription ID `sub_...` | `shu-secrets.stripe-subscription-id` |

**Always start the subscription quantity at 1** — see [STRIPE_SETUP.md Part 1.6.1](STRIPE_SETUP.md#161-subscription-quantity-rule-always-start-at-1). Shu's quantity-sync scheduler brings the Stripe seat quantity up to match the tenant's actual active user count within the first minute after boot.

> **Gap — per-customer allowance override**
> If a customer negotiated a non-default included-usage allowance (e.g., enterprise deal with $100/seat instead of the $50 default), set it per-tenant once SHU-706 lands a control-plane API. Until then: direct SQL `UPDATE billing_state SET included_usd_per_user = X WHERE id = 1;` on the tenant's database after first boot. Track the override in your operator log so it survives DB migrations.

> **Gap — minimum seat floor for contractual commitments**
> SHU-709 adds an optional `billing_state.minimum_quantity` for enterprise deals with seat commitments. Until it lands, contractual seat floors are enforced manually (don't downgrade below the commit) rather than at the subscription layer.

## External provider configuration

### OpenRouter

LLM and embedding traffic routes through a single OpenRouter account on the Shu side, billed to the Shu account and then re-billed to customers via Shu's cost metering. Each tenant uses the same OpenRouter API key.

> **Gap — BYOK flow**
> SHU-705 adds `is_shu_managed` provenance so customers can attach their own OpenRouter/OpenAI/Anthropic provider keys for their internal LLM traffic without it flowing through Shu's billing. Until SHU-705 lands, BYOK tenants are not operationally supported; every tenant uses the Shu-account OpenRouter key.

The OpenRouter API key is typically shared across tenants via the deployment overlay's secret. Rotate it at the account level; tenants pick up the rotation on next pod restart.

### Mistral

OCR traffic routes directly to `api.mistral.ai` (not through OpenRouter). Single Mistral account, single API key, shared across tenants in the same way as OpenRouter.

> **Gap — Mistral OCR usage recording**
> SHU-711 fixes the provider-name mismatch that's silently dropping OCR `llm_usage` rows. Until it lands, OCR extractions work but their cost is not metered and customers are under-billed for OCR. Must be fixed before go-live.

## Deployment overlay

> **Gap — hosted-offering overlay**
> A dedicated `shu-deploy/kubernetes/hosted/` (or equivalent) overlay has not yet been built. The existing `demo/` overlay targets a single shared environment and is not structured around per-tenant provisioning. Building the hosted overlay is part of SHU-667 (Multi-Tenant Infrastructure Setup). Concrete pieces that need to exist:
>
> - Per-tenant namespace (e.g., `shu-tenant-<slug>`) and associated RBAC.
> - ConfigMap + Secret templated per-tenant (Stripe customer/subscription IDs, domain, etc.).
> - `shu-api` + `shu-frontend` deployments with images tagged per release.
> - Ingress + TLS via cert-manager + real `ClusterIssuer` (unlike the lab which runs without TLS).
> - Connection config for the **external Postgres** (managed database, not in-cluster) with per-tenant database or schema.
> - Connection config for the **shared Redis** with per-tenant key namespacing (see SHU-691).
> - Image-pull secrets if images are in a private registry.
>
> The shared infrastructure pattern from `common-local/` (built during SHU-699) is a good structural reference; extract the truly reusable pieces into `common-hosted/` once the hosted topology decisions land.

Once the overlay is built, per-tenant provisioning looks like:

```bash
# Example shape, actual command TBD
kubectl apply -k kubernetes/hosted/tenants/<tenant-slug>/
```

## Secret provisioning

> **Gap — secret management strategy**
> Committing per-tenant secrets to the `shu-deploy` repo (as the lab does for `shu-secrets.yaml`) does not scale to multi-tenant and violates basic secret-hygiene norms. The hosted offering needs either (a) a sealed-secrets controller (bitnami-labs/sealed-secrets), (b) external-secrets integrated with a cloud vault, or (c) an operator-run control plane that writes secrets directly to the cluster via HMAC-signed API. Choice depends on SHU-667 architectural decisions. Until then, operators hand-create `Secret` resources via `kubectl create secret` out of band and do not commit the manifest.

Per-tenant secret fields (same shape as the lab's `shu-secrets.yaml`):

| Field | Source | Notes |
|---|---|---|
| `database-url` | External Postgres per-tenant | `postgresql+asyncpg://<user>:<pass>@host:5432/tenant_<slug>` |
| `jwt-secret-key` | Generate per tenant | Different from other tenants |
| `llm-encryption-key` | Generate per tenant | Different from other tenants |
| `oauth-encryption-key` | Generate per tenant | Different from other tenants |
| `stripe-secret-key` | Shu-account restricted key | Same for all tenants |
| `router-shared-secret` | Generated when the tenant is registered with the control plane | Unique per tenant; 64 lowercase hex |
| `stripe-customer-id` | Per-customer Stripe setup | Unique per tenant |
| `stripe-subscription-id` | Per-customer Stripe setup | Unique per tenant |
| `openrouter-api-key` | Shu-account OpenRouter key | Same for all tenants |
| `mistral-ocr-api-key` | Shu-account Mistral key | Same for all tenants |

## Tenant registration with the control plane

Stripe webhooks are received by the **Shu Control Plane**, a single central webhook router service. There is exactly one Stripe webhook endpoint registered in the Stripe dashboard per environment (test, live) — it points at the control plane. Tenants never appear in the Stripe dashboard webhooks list, and the Stripe 16-endpoint-per-account cap is irrelevant for scaling tenants.

The operational unit of "adding a customer to webhook delivery" is registering a row in the control-plane `tenant` table. The row maps `stripe_customer_id → (instance_url, shared_secret)`:

- `stripe_customer_id` — the `cus_...` id Stripe sends in event payloads.
- `instance_url` — the full URL the control plane POSTs forwarded events to. Internal cluster DNS in K8s (e.g. `http://shu-api.shu-tenant-<slug>.svc.cluster.local/api/v1/billing/webhooks`).
- `shared_secret` — 64 lowercase hex chars. The control plane uses it to sign forwarded envelopes (HMAC-SHA256 over timestamp + method + path + body); the tenant uses it to verify.
- `status` — `active`, `disabled`, or `unprovisioned`. Only `active` tenants receive forwarded events.

### Registration procedure

> **Gap — registration tooling**
> Production-grade tenant registration (CLI or API against the control plane, tied into an onboarding flow) is tracked as a SHU-696 follow-up. Until that lands, registration is a direct SQL INSERT against the control-plane `shu_cp` database. The lab runbook documents the exact psql flow in its "Seed the tenant registry" section — the same pattern works for hosted environments, just pointed at the hosted control-plane's Postgres.

Registration writes to two places; both must be kept in sync:

1. **Control-plane `tenant` table**: INSERT row with the generated `shared_secret`.
2. **Tenant `Secret`** (`shu-secrets` in the tenant's namespace): set `router-shared-secret` to the same value, then restart `shu-api` so it picks up the new `SHU_ROUTER_SHARED_SECRET`.

The Stripe account-level webhook endpoint is configured once (not per tenant) — see [STRIPE_SETUP.md Going Live](STRIPE_SETUP.md#going-live) for the Dashboard procedure.

## Apply, migrate, and seed

Once the overlay + secrets exist for the tenant:

```bash
kubectl apply -k kubernetes/hosted/tenants/<tenant-slug>/
kubectl -n shu-tenant-<slug> wait --for=condition=complete job/shu-db-migrate --timeout=10m
kubectl -n shu-tenant-<slug> logs job/shu-db-migrate | tail -30
```

Expected tail of migration logs:

```text
Migrations complete
Running hosting deployment seed ...
[hosting] Created provider 'OpenAI' (id=...)
[hosting] Created provider 'Anthropic' (id=...)
[hosting] Created model 'z-ai/glm-5.1'
...
[hosting] Hosting deployment seed complete
```

Then wait for pods:

```bash
kubectl -n shu-tenant-<slug> rollout status deployment/shu-api --timeout=5m
kubectl -n shu-tenant-<slug> rollout status deployment/shu-frontend --timeout=5m
```

Verify `billing_state` row was seeded:

```bash
# Connect to external Postgres, per-tenant DB.
# base64 -d works on GNU coreutils (Linux) and modern macOS; on older macOS
# use -D, or use --decode for GNU. The `|| base64 -D` fallback is portable.
psql "$(kubectl -n shu-tenant-<slug> get secret shu-secrets -o jsonpath='{.data.database-url}' | { base64 -d 2>/dev/null || base64 -D; })" \
  -c "SELECT stripe_customer_id, stripe_subscription_id, subscription_status FROM billing_state;"
```

Expected: `stripe_customer_id` and `stripe_subscription_id` match what you put in the secret. `subscription_status='pending'` until the first webhook delivers.

## First-webhook verification

Trigger a no-op subscription update on the customer's sub:

```bash
stripe subscriptions update <sub_id> -d 'metadata[provisioning]=hello'
```

Observe a 200 response at the Stripe Dashboard webhook endpoint (the control plane). In the tenant DB:

```bash
psql "..." -c "SELECT subscription_status, quantity, current_period_start, current_period_end FROM billing_state;"
```

Expected: `subscription_status='active'`, quantity populated (typically 0 or 1), periods populated.

If still `pending`: check the control-plane logs for the outcome (`kubectl -n shu-control-plane logs deployment/shu-control-plane --tail=20 | grep 'webhook routed'`). Common failure modes: `unknown_customer` (tenant not registered), `tenant_error` with 401 (HMAC secret mismatch between control-plane registry and tenant `SHU_ROUTER_SHARED_SECRET`), `tenant_error` with 409 (`stripe_customer_id` mismatch between registry row and tenant `SHU_STRIPE_CUSTOMER_ID`).

## Admin user setup

> **Gap — customer first-login handoff**
> The precise operator-vs-customer handoff for admin account creation is not codified. Three plausible flows:
>
> 1. **Customer self-registers** as the first user on a fresh instance — current auto-admin-on-first-user behavior kicks in, they get admin rights. Requires the tenant's domain to be operational and the customer to know to register first.
> 2. **Operator pre-creates** an admin account using the customer's provided email + a temporary password, then forces password-reset on first login. Requires a password-reset flow to exist.
> 3. **Operator invites** via Google OAuth by adding the customer's Google email to `ADMIN_EMAILS` in the tenant's configmap before boot. Requires Google OAuth to be configured for the tenant.
>
> Pick one and document it here; I don't have enough context to choose. SHU-663 epic likely has a preference that I haven't seen.

Interim manual flow:

1. Add the customer's email to `ADMIN_EMAILS` in the tenant's configmap.
2. Apply + restart `shu-api`.
3. Share the tenant's URL with the customer and have them register via the frontend. Auto-activate (`SHU_AUTO_ACTIVATE_USERS=true`, if set) or `ADMIN_EMAILS` match auto-promotes them.
4. Customer logs in, changes password (if password auth).

## Going live (test mode → live mode)

> **Gap — live-mode procedure rewrite**
> [STRIPE_SETUP.md Going Live](STRIPE_SETUP.md#going-live) has a procedure for switching a Shu instance from `pk_test_` / `sk_test_` / `rk_test_` to the live-mode equivalents, but it references a grace-period suspension behavior (around `SHU_STRIPE_PAYMENT_GRACE_DAYS`) that was AI-authored and does not reflect deliberate product direction. SHU-703 defines the intended enforcement model: driven by Stripe `subscription.status` with a default grace of 0 (strict), scoped to the retryable `past_due` status. Once SHU-703 lands, rewrite the Going Live section against that model and cross-link here.

High-level procedure (pending SHU-703 rewrite):

1. In Stripe dashboard, switch from Test Mode to Live Mode.
2. Recreate the same Part 1 resources (product, prices, meter) in live mode.
3. Recreate per-customer Part 2 resources for each live customer.
4. Update each tenant's `shu-secrets` and `configmap` with `_live` keys and IDs.
5. Register the single live-mode Stripe webhook endpoint pointing at the live control plane (one per Stripe account, not per tenant). Configure `SHU_CP_STRIPE_WEBHOOK_SECRET` on the live control plane with the endpoint's signing secret. See [STRIPE_SETUP.md Going Live](STRIPE_SETUP.md#going-live).
6. Register each live-mode tenant with the live control plane (INSERT tenant row, sync `SHU_ROUTER_SHARED_SECRET` to the tenant's secret).
7. Roll the tenant pod and verify.

## Operational procedures

### Monitoring usage

For each tenant: usage flows `llm_usage` → reporter → Stripe meter. Check alignment:

```bash
psql "..." -c "SELECT last_reported_total, last_reported_period_start FROM billing_state;"
```

Compare against Stripe dashboard → Customer → Billing Meters → usage total for the current period. Microdollars on Shu side ≈ events on Stripe side (modulo rounding per SHU-682).

### Overriding per-tenant billing parameters

> **Gap — operator override API**
> SHU-706 (control-plane HMAC-signed PATCH endpoint) is the supported mechanism for runtime overrides of per-tenant allowance, minimum seat count, etc. Until it lands, overrides are manual SQL:
>
> ```sql
> UPDATE billing_state SET included_usd_per_user = 100.00 WHERE id = 1;
> ```
>
> Document every manual override in your operator log with tenant slug, value, and reason.

### Handling payment failures

> **Gap — SHU-703 enforcement not implemented**
> Current codebase writes `payment_failed_at` on `invoice.payment_failed` but never reads it; enforcement is silent. SHU-703 adds subscription-status-driven blocking (chat + ingestion blocked on `past_due`/`unpaid`/`canceled`, banner directs customer to Stripe Portal). Until SHU-703 lands, operators should monitor Stripe's failed-invoice webhooks manually and contact customers out of band.

### Handling seat-count changes

Customer adds users → Shu auto-syncs via quantity-sync scheduler within 60s. No operator action needed.

Customer wants to reduce seats → per SHU-704, reduction is deferred to next period end. Operator action is informational only (confirm the customer understands they're paying the current period at the current seat count).

> **Gap — SHU-704 not implemented**
> Deferred-downgrade behavior + per-seat included-usage Credit Grants are specified but not yet implemented. Until SHU-704 lands, treat every seat-count change as an immediate proration event (Stripe default) and manually manage included-usage expectations. Expect customer friction around "I bought 5 seats but only used 2" scenarios.

## Tenant offboarding

> **Gap — offboarding policy**
> The data retention policy for offboarded tenants is not defined. Open questions: how long are chats retained? KBs? `llm_usage` history? Is data handed back to the customer on request? Is it purged on a schedule? Answers belong in a compliance doc, not this runbook, but this runbook should link to that doc once it exists.

Interim procedure (data-destructive):

1. Cancel the customer's Stripe subscription in the dashboard. Set `cancel_at_period_end=true` for grace period; set `cancel_immediately=true` for hard cutoff.
2. Once the subscription is fully canceled, delete the tenant's namespace:

   ```bash
   kubectl delete namespace shu-tenant-<slug>
   ```

3. Drop the tenant's Postgres database (if using per-tenant DBs on external Postgres).
4. Remove the tenant from the control-plane registry:

   ```sql
   UPDATE tenant SET status = 'disabled' WHERE stripe_customer_id = 'cus_...';
   -- or, after a retention window:
   DELETE FROM tenant WHERE stripe_customer_id = 'cus_...';
   ```

   Disabling first (rather than deleting) preserves audit trail and causes the control plane to return `tenant_inactive` for any in-flight events. Delete once the retention window has passed.
5. Revoke any tenant-specific credentials.

All of the above is destructive; have a data-export path ready before executing if the customer has requested their data.
