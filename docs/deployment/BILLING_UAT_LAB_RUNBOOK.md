# Billing UAT Lab Runbook

End-to-end procedure for bringing up the `local-billing-lab` Kubernetes overlay, wiring it to Stripe + OpenRouter + Mistral, and validating the billing pipeline. This is the operator-facing counterpart to [STRIPE_SETUP.md](STRIPE_SETUP.md) — Stripe setup lives there, K8s setup lives here, and both are needed for a full lab.

## Table of contents

1. [Purpose and scope](#purpose-and-scope)
2. [Prerequisites](#prerequisites)
3. [External account setup](#external-account-setup)
4. [Populate secrets](#populate-secrets)
5. [Apply the lab overlay](#apply-the-lab-overlay)
6. [Run migrations and hosting seed](#run-migrations-and-hosting-seed)
7. [Verify the stack](#verify-the-stack)
8. [Register an admin user](#register-an-admin-user)
9. [Start Stripe CLI webhook forwarding](#start-stripe-cli-webhook-forwarding)
10. [Smoke test the billing pipeline](#smoke-test-the-billing-pipeline)
11. [Known gaps at current codebase](#known-gaps-at-current-codebase)
12. [Teardown](#teardown)
13. [Troubleshooting](#troubleshooting)

## Purpose and scope

The `local-billing-lab` overlay is a single-tenant, docker-desktop–deployable instance of Shu with the full Stripe billing module wired up against live Stripe test-mode APIs. It is the environment that validates every billable surface (LLM chat, embeddings, OCR) and every Stripe lifecycle event (subscription creation, quantity sync, usage reporting, webhook delivery).

This runbook is what a **new** operator (someone who has never deployed Shu before) follows to reach a working lab. No tribal knowledge is assumed. Where a step is covered authoritatively in another doc, this runbook links to it rather than duplicating.

The lab is **test-mode only**. Live-mode configuration is out of scope; see the Going Live section of STRIPE_SETUP.md when the time comes.

## Prerequisites

Local tooling:

- Docker Desktop with Kubernetes enabled (`kubectl config current-context` returns `docker-desktop`).
- `kubectl` 1.28+ (any recent version works).
- `stripe` CLI (`brew install stripe/stripe-cli/stripe`, then `stripe login`). On macOS Tahoe with CLT issues, use the [Stripe CLI binary download](https://github.com/stripe/stripe-cli/releases) instead of Homebrew.
- `make` (for the `*-local-lab` targets in `shu-deploy/Makefile`).
- `psql` (installed as part of most Postgres toolchains — used only for manual DB checks, not required for the main flow).

Repository checkouts under a shared workspace directory:

- `shu-deploy/` — kustomize overlays.
- `shu/` — backend source, docs, and this runbook.

External accounts:

- A Stripe account with test mode enabled.
- An OpenRouter account and API key.
- A Mistral account and API key (only used for OCR).

Cluster resources needed:

- Docker Desktop memory allocation ≥ 6 GiB (Preferences → Resources).
- ~10 GiB free disk for image pulls + PVC data.

## External account setup

### Stripe dashboard (one-time per account)

Follow [STRIPE_SETUP.md Part 1](STRIPE_SETUP.md#part-1-stripe-dashboard-setup-once) end-to-end. Required outputs from that procedure, used below:

| Output | Used in |
|---|---|
| Restricted secret key `rk_test_...` | `shu-secrets.yaml` → `stripe-secret-key` |
| Publishable key `pk_test_...` | `configmap.yaml` → `SHU_STRIPE_PUBLISHABLE_KEY` |
| Product ID `prod_...` | `configmap.yaml` → `SHU_STRIPE_PRODUCT_ID` |
| Per-seat price ID `price_...` | `configmap.yaml` → `SHU_STRIPE_PRICE_ID_MONTHLY` |
| Cost meter ID `mtr_test_...` | `configmap.yaml` → `SHU_STRIPE_METER_ID_COST` |
| Cost meter event name (e.g. `usage_cost`) | `configmap.yaml` → `SHU_STRIPE_METER_EVENT_NAME` |

### Stripe customer + subscription (one-time per tenant)

Follow [STRIPE_SETUP.md Part 2](STRIPE_SETUP.md#part-2-customer-subscription-setup-per-customer). Outputs:

| Output | Used in |
|---|---|
| Test customer ID `cus_...` | `shu-secrets.yaml` → `stripe-customer-id` |
| Subscription ID `sub_...` | `shu-secrets.yaml` → `stripe-subscription-id` |

Start the subscription quantity at **1** — see [STRIPE_SETUP.md Part 1.6.1](STRIPE_SETUP.md#161-subscription-quantity-rule-always-start-at-1) for why.

### OpenRouter

1. Sign in at <https://openrouter.ai> and create a scoped API key for this lab.
2. Note the key — it'll go into `shu-secrets.yaml` as `openrouter-api-key`.
3. Confirm your account has credit or a payment method attached; OpenRouter requires a small minimum for chat/embedding calls.

### Mistral

1. Sign in at <https://console.mistral.ai> and create an API key.
2. Note the key — it'll go into `shu-secrets.yaml` as `mistral-ocr-api-key`.

Note: Mistral OCR does **not** route through OpenRouter. It talks directly to `api.mistral.ai`. The hosting seeder contains a misleading comment that says otherwise; SHU-711 tracks the fix for that plus the downstream impact on OCR usage recording.

## Populate secrets

The lab overlay ships a committed example at `shu-deploy/kubernetes/local-billing-lab/shu-secrets.yaml.example`. Copy it to the real secret file, which is gitignored:

```bash
cd /path/to/shu-workspace/shu-deploy/kubernetes/local-billing-lab
cp shu-secrets.yaml.example shu-secrets.yaml
```

Then edit `shu-secrets.yaml` and fill in the base64-encoded values for each field. Reference commands:

```bash
# Encode a value for the secret:
printf 'sk_test_abcd...' | base64
# Decode a value to verify it's correct:
echo 'c2tfdGVzdF9hYmNkLi4u' | base64 -d
```

Fields that need real values (all other fields in the example are lab-appropriate defaults):

| Field | Source |
|---|---|
| `stripe-secret-key` | Stripe dashboard Part 1.1 |
| `stripe-webhook-secret` | Printed by `stripe listen` the first time you run it (step below); update after |
| `stripe-customer-id` | Stripe dashboard Part 2.1 |
| `stripe-subscription-id` | Stripe dashboard Part 2.3 |
| `openrouter-api-key` | OpenRouter dashboard |
| `mistral-ocr-api-key` | Mistral console |
| `google-client-secret` | Only if you want Google OAuth login; otherwise leave blank |

The webhook secret becomes available during [Start Stripe CLI webhook forwarding](#start-stripe-cli-webhook-forwarding). Populate it into the secret, then `kubectl apply` or re-run `make apply-local-lab` to push the updated value.

## Apply the lab overlay

From `shu-deploy/`:

```bash
make apply-local-lab
```

This runs `kubectl apply -k kubernetes/local-billing-lab`. It creates:

- Namespace `shu-billing-lab`
- ConfigMap and Secret
- Deployments: `postgres`, `redis`, `shu-api`, `shu-frontend`
- Services and the Gateway API `HTTPRoute`
- Migration Job (`shu-db-migrate`)

You will see one harmless apply-time error:

```
error: resource mapping not found for name: "letsencrypt-prod" namespace: "shu-billing-lab" from "kubernetes/local-billing-lab": no matches for kind "ClusterIssuer" in version "cert-manager.io/v1"
```

This is because cert-manager isn't installed on docker-desktop and the `ClusterIssuer` CRD doesn't exist. The lab doesn't use TLS so this is expected. The rest of the apply succeeds despite this error.

Wait for pods:

```bash
make rollout-status-local-lab
```

Expected final state:

```text
NAME                            READY   STATUS      RESTARTS   AGE
postgres-...                    1/1     Running     0          ...
redis-...                       1/1     Running     0          ...
shu-api-...                     1/1     Running     0          ...
shu-frontend-...                1/1     Running     0          ...
shu-db-migrate-...              0/1     Completed   0          ...
```

## Run migrations and hosting seed

The migration Job runs automatically during the first `apply`. It performs both Alembic migrations **and** the hosting deployment seed (creates LLM provider rows, seeds chat/embedding/OCR model configurations).

If you need to re-run (e.g. after changing seed env):

```bash
make migrate-local-lab
```

Verify in the Job logs:

```bash
kubectl -n shu-billing-lab logs job/shu-db-migrate | tail -30
```

Expected tail (model IDs may differ if you customized `SHU_SEED_MODELS`):

```text
Migrations complete
Running hosting deployment seed ...
[hosting] Created provider 'OpenAI' (id=...)
[hosting] Created provider 'Anthropic' (id=...)
[hosting] Created model 'z-ai/glm-5.1'
[hosting] Created model configuration 'GLM 5.1' (...)
...
[hosting] Created embedding model 'openai/text-embedding-3-small' (dimension=1536)
[hosting] Created OCR model 'mistral-ocr-latest'
[hosting] Hosting deployment seed complete
```

## Verify the stack

Four checks confirm the stack is operational before you start exercising billing.

### 1. `billing_state` seeded

```bash
kubectl -n shu-billing-lab exec deployment/postgres -- \
  psql -U shu -d shu -c "SELECT id, stripe_customer_id, stripe_subscription_id, subscription_status, quantity FROM billing_state;"
```

Expected:

```text
 id | stripe_customer_id |    stripe_subscription_id    | subscription_status | quantity
----+--------------------+------------------------------+---------------------+----------
  1 | cus_...            | sub_...                      | pending             |        0
```

`subscription_status='pending'` is expected here — it transitions to `active` once the first webhook is delivered. If the row is missing or fields are NULL, the seed didn't run; see [Troubleshooting](#troubleshooting).

### 2. External OCR active

Check the persistent log on the data volume (survives pod restarts):

```bash
kubectl -n shu-billing-lab exec deployment/shu-api -- \
  sh -c 'grep "Using external OCR service" /app/data/logs/*.log | tail -1'
```

Expected:

```text
... - INFO - Using external OCR service (Mistral OCR) | model=mistral-ocr-latest base_url=https://api.mistral.ai/v1
```

If empty, OCR hasn't been exercised yet — that line only fires on the first call to `get_ocr_service()`. Confirm again after the first OCR'd document in [Smoke test](#smoke-test-the-billing-pipeline).

### 3. `/billing/config` returns expected payload

Register an admin and call the endpoint (see [next section](#register-an-admin-user)). The endpoint requires admin JWT — STRIPE_SETUP.md Part 3.1 documents this is admin-protected, not public.

### 4. Stack memory footprint within expected bounds

```bash
docker stats --no-stream --format "{{.Name}}\t{{.MemUsage}}" | grep -E "shu-api|postgres"
```

Expected idle: `shu-api` around 170 MiB, `postgres` around 100–140 MiB. Under active ingestion this rises. If shu-api is near 1 GiB at idle, check that `SHU_LOCAL_EMBEDDING_ENABLED=false` is in effect (the default sentence-transformers preload is ~850 MiB resident and is expected to be disabled in this overlay).

## Register an admin user

The first user registered automatically becomes `admin + active` regardless of `ADMIN_EMAILS`. Open the frontend:

```text
http://localhost/
```

Register with your email. If auto-activate is off or the user is flagged inactive, check that `SHU_AUTO_ACTIVATE_USERS=true` is present in the configmap and that no prior users exist (the auto-admin-first-user behavior requires a zero-users-state).

Obtain a JWT (for CLI testing):

```bash
curl -sS -X POST http://localhost/api/v1/auth/login/password \
  -H "Content-Type: application/json" \
  -d '{"email":"you@your-domain.com","password":"YOUR_PASSWORD"}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['data']['access_token'])"
```

Export for convenience:

```bash
export JWT='<paste-token-here>'
```

Now verify `/billing/config`:

```bash
curl -sS -H "Authorization: Bearer $JWT" http://localhost/api/v1/billing/config
```

Expected:

```json
{"data":{"configured":true,"publishable_key":"pk_test_...","mode":"test"}}
```

## Start Stripe CLI webhook forwarding

In a separate terminal:

```bash
stripe listen --forward-to http://localhost/api/v1/billing/webhooks
```

First run prints:

```text
> Ready! You are using Stripe API Version [2026-03-25.dahlia]. Your webhook signing secret is whsec_...
```

Copy that `whsec_...` value — it goes into `shu-secrets.yaml` as `stripe-webhook-secret`. Re-apply the secret:

```bash
make apply-local-lab
kubectl -n shu-billing-lab rollout restart deployment/shu-api
kubectl -n shu-billing-lab rollout status deployment/shu-api --timeout=90s
```

Leave the `stripe listen` terminal running for the rest of the lab session. Shu verifies the signature on every inbound webhook using this secret.

## Smoke test the billing pipeline

### 1. First webhook delivery — status transition

Trigger a no-op subscription update on your actual subscription:

```bash
stripe subscriptions update sub_... -d 'metadata[uat]=smoke-1'
```

Within a second or two, the `stripe listen` window should show:

```text
... --> customer.subscription.updated [evt_...]
... <--  [200] POST http://localhost/api/v1/billing/webhooks [evt_...]
```

A 200 return code confirms three things at once: the middleware is not blocking the webhook, Stripe signature verification succeeded, and `parse_subscription_update` parsed the payload (Stripe API version 2026-03-25.dahlia is pinned in `StripeClient._configure_stripe`, which matches the CLI's reported version).

Confirm the DB state transition:

```bash
kubectl -n shu-billing-lab exec deployment/postgres -- \
  psql -U shu -d shu -c "SELECT subscription_status, quantity, current_period_start, current_period_end FROM billing_state;"
```

Expected: `subscription_status='active'`, `quantity=1`, periods populated.

### 2. Quantity sync

Create a second user via the admin API:

```bash
curl -sS -X POST http://localhost/api/v1/auth/users \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"email":"user2@your-domain.com","password":"userpass123!","name":"User Two","role":"regular_user"}'
```

The scheduler's `billing_quantity_sync` source runs every 60s. Within a minute, check:

```bash
kubectl -n shu-billing-lab logs deployment/shu-api --tail=200 | grep "Quantity sync completed"
```

Expected: `Quantity sync completed | subscription_id=sub_... user_count=2`. Confirm in Stripe Dashboard → Customers → your customer → Subscriptions → Items → seat price quantity = 2.

### 3. LLM chat + embedding + OCR

In the frontend:

1. Create a knowledge base.
2. Upload at least one text-layered PDF (triggers `pdf_text` + embedding + profiling).
3. Upload at least one scanned-image PDF (triggers Mistral OCR + embedding + profiling).
4. Send a chat message against a seeded model (`GLM 5.1`, `Gemma 4 31B`, or `Auto (OpenRouter)`).

Confirm `llm_usage` is being written:

```bash
kubectl -n shu-billing-lab exec deployment/postgres -- psql -U shu -d shu -c \
  "SELECT request_type, count(*), sum(total_cost) FROM llm_usage GROUP BY request_type ORDER BY request_type;"
```

Expected rows for `chat`, `embedding`, and `side_call`. See [Known gaps](#known-gaps-at-current-codebase) for why `ocr` may be missing and why `chat/side_call.total_cost` may be 0.

### 4. Usage report → Stripe meter

By default the scheduler pushes usage deltas to the Stripe meter every 60s in the lab (`SHU_STRIPE_USAGE_REPORT_INTERVAL=60`). After generating embedding activity (step 3), confirm on the Stripe side:

```bash
kubectl -n shu-billing-lab exec deployment/postgres -- psql -U shu -d shu -c \
  "SELECT last_reported_total, last_reported_period_start FROM billing_state;"
```

`last_reported_total` (in microdollars) should grow over time and equal (modulo rounding) `SUM(llm_usage.total_cost) * 1000000`. On the Stripe Dashboard, navigate to Customers → your customer → Billing Meters → your usage meter, and confirm the event-summary total matches.

### 5. Customer portal

```bash
curl -sS -H "Authorization: Bearer $JWT" http://localhost/api/v1/billing/portal
```

Returns a Stripe-hosted billing-portal URL. Open it to see the customer-facing invoice + subscription view.

## Known gaps at current codebase

At the time this runbook was written, the lab setup works end-to-end but the following known gaps mean some verifications will fall short of ideal:

- **SHU-700** — chat and side_call costs come from a hardcoded `model_pricing.py` table, which doesn't include most OpenRouter model IDs (`z-ai/*`, `google/*`, `openrouter/*`). Expect `llm_usage.total_cost=0` on those rows until SHU-700 lands; only `openai/text-embedding-3-small` embedding calls currently produce non-zero cost.
- **SHU-711** — Mistral OCR usage is never recorded in `llm_usage` because of a provider-name mismatch between the hosting seeder and the OCR service's resolver. OCR extractions succeed; usage rows don't appear; no OCR cost reaches the meter.
- **SHU-697 / SHU-712** — the hosted offering will front `shu-api` with a webhook router (Stripe → router → shu-api). The lab currently uses direct `/api/v1/billing/webhooks` via a `middleware.public_paths` workaround committed explicitly as a placeholder. Once SHU-712 lands the router integration, the lab topology changes and this runbook needs a `Stripe CLI → router → shu-api` section.
- **SHU-704** — per-seat included-usage allowance via Stripe Credit Grants, plus the asymmetric upgrade-immediate / downgrade-deferred seat policy. Not implemented yet; scenarios 5b/5c and 19 in the SHU-699 matrix will show `known-gap` until SHU-704 ships.
- **SHU-705** — Shu-managed vs BYOK provider provenance for the billing filter. Not implemented yet; scenarios 20/21 will show `known-gap`.

None of these gaps block the lab from being stood up and exercised. They define the ceiling of what you can validate until the respective ticket ships.

## Teardown

Full reset (namespace + PVCs):

```bash
make delete-local-lab
kubectl -n shu-billing-lab delete pvc --all  # if the namespace deletion didn't already clean these
```

Lab data (chat history, documents, usage rows) is on the `shu-data-pvc` and is destroyed with the PVC. Stripe-side objects (the customer, subscription, events) persist in your Stripe test account independently; manage them from the Stripe dashboard if you need to reset.

## Troubleshooting

### Pod `postgres` CrashLoopBackOff with "PostgreSQL data in /var/lib/postgresql/data (unused mount/volume)"

The paradedb pg18 image requires the PVC mounted at `/var/lib/postgresql` (parent), not `/var/lib/postgresql/data`. Check `common-local/postgres-deployment.yaml` — the `volumeMounts.mountPath` should be `/var/lib/postgresql`. If an older overlay version is in the cluster, re-apply with the current overlay.

### Pod `postgres` ImagePullBackOff for `pgvector/pgvector:latest-pg18`

That tag doesn't exist on Docker Hub. The correct canonical image is `paradedb/paradedb:latest-pg18`, defined in `common-local/postgres-deployment.yaml`. Verify the overlay you're applying has that image.

### Webhook forwarder returns `[401]` from shu-api

If you see this during `stripe listen` output after a trigger, the `middleware.public_paths` entry for `/api/v1/billing/webhooks` is missing. Check `shu/backend/src/shu/core/middleware.py` has `"/api/v1/billing/webhooks"` in `AuthenticationMiddleware.public_paths`. This is a lab-only workaround that SHU-712 will retire.

### Webhook 500s with `KeyError: 'current_period_start'`

Pre-SHU-707 code is parsing Stripe payloads incorrectly against API version 2026-03-25.dahlia, which moved `current_period_*` from the Subscription to individual SubscriptionItems. Confirm `parse_subscription_update` in `shu/backend/src/shu/billing/stripe_client.py` reads from the resolved `seat_item`, and confirm `_configure_stripe` pins `stripe.api_version = "2026-03-25.dahlia"`. Both landed in commit `4a8d47a`.

### `billing_state` row missing after first boot

Usually a postgres-not-ready race during initial apply. Restart shu-api once Postgres is healthy:

```bash
kubectl -n shu-billing-lab rollout restart deployment/shu-api
```

The seed path is idempotent — it creates the singleton only if missing, and writes env-seeded fields only if the target columns are still at their defaults.

### Knowledge base marked `embedding_status='stale'` but chunks are embedded correctly

Pre-SHU-708 code flagged the KB stale on model-mismatch during startup but never cleared the flag when the first document's ingestion corrected the model. Confirm the fix is in the running image (commit `4a8d47a`). For existing labs with already-populated KBs, clear the flag manually:

```bash
kubectl -n shu-billing-lab exec deployment/postgres -- \
  psql -U shu -d shu -c "UPDATE knowledge_bases SET embedding_status='current' WHERE embedding_status='stale';"
```

The SHU-708 fix self-heals on *first-doc-ingest*, so a KB that already has chunks won't self-repair; SQL is the supported operator workaround.

### shu-api memory spikes to ~900 MiB at idle

`SHU_LOCAL_EMBEDDING_ENABLED` is probably defaulting to `true`. The lab overlay sets it to `"false"` in `configmap.yaml`. Confirm:

```bash
kubectl -n shu-billing-lab exec deployment/shu-api -- sh -c 'echo $SHU_LOCAL_EMBEDDING_ENABLED'
```

If this prints empty or `true`, re-apply the overlay and restart shu-api.

### OCR log line never appears

`get_ocr_service()` is called lazily on the first OCR-needing document. If you've only uploaded text-layered PDFs, no OCR has happened. Upload a scanned-image PDF to exercise the path, then re-check:

```bash
kubectl -n shu-billing-lab exec deployment/shu-api -- \
  sh -c 'grep "Using external OCR service" /app/data/logs/*.log | tail -1'
```

### `stripe listen` websocket closed unexpectedly

Stripe CLI refreshes the forwarding websocket periodically. Restart `stripe listen`. If you see 401s on restart, the webhook secret printed by the new session is the same as before (CLI caches it), so the secret in `shu-secrets.yaml` remains valid — no re-apply needed.

### More

[STRIPE_SETUP.md Troubleshooting](STRIPE_SETUP.md#troubleshooting) covers additional scenarios that overlap with Stripe-dashboard setup issues.
