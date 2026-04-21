# Billing UAT Lab Runbook

End-to-end procedure for bringing up the full Stripe-billing lab (tenant shu-api + Shu Control Plane webhook router + shared Envoy gateway) on Kubernetes, wiring it to Stripe + OpenRouter + Mistral, and validating the billing pipeline. This is the operator-facing counterpart to [STRIPE_SETUP.md](STRIPE_SETUP.md) — Stripe setup lives there, K8s setup lives here, and both are needed for a full lab.

The lab reflects the hosted-offering topology: Stripe posts webhooks to a single control-plane endpoint, which verifies the Stripe signature, looks up the target tenant, and forwards the event under an HMAC envelope. The tenant's shu-api accepts envelopes signed with a per-tenant shared secret and no longer verifies Stripe signatures directly.

## Table of contents

1. [Purpose and scope](#purpose-and-scope)
2. [Prerequisites](#prerequisites)
3. [External account setup](#external-account-setup)
4. [Populate tenant secrets (shu-billing-lab)](#populate-tenant-secrets-shu-billing-lab)
5. [Populate control-plane secrets (shu-control-plane)](#populate-control-plane-secrets-shu-control-plane)
6. [Apply the lab overlays](#apply-the-lab-overlays)
7. [Run migrations and seeds](#run-migrations-and-seeds)
8. [Seed the tenant registry and the router shared secret](#seed-the-tenant-registry-and-the-router-shared-secret)
9. [Verify the stack](#verify-the-stack)
10. [Register an admin user](#register-an-admin-user)
11. [Start Stripe CLI webhook forwarding](#start-stripe-cli-webhook-forwarding)
12. [Smoke test the billing pipeline](#smoke-test-the-billing-pipeline)
13. [Known gaps at current codebase](#known-gaps-at-current-codebase)
14. [Teardown](#teardown)
15. [Troubleshooting](#troubleshooting)

## Purpose and scope

The lab is a single-tenant, docker-desktop–deployable instance of Shu with the full Stripe billing module wired up against live Stripe test-mode APIs. It is the environment that validates every billable surface (LLM chat, embeddings, OCR) and every Stripe lifecycle event (subscription creation, quantity sync, usage reporting, webhook delivery). It also validates the control-plane webhook router that fronts every tenant in the hosted offering.

The lab runs three cooperating overlays in three namespaces:

| Namespace | Overlay | Role |
|---|---|---|
| `shu-gateway` | `shu-deploy/kubernetes/shu-gateway/` | Shared Envoy Gateway with two listeners — port 80 for tenant traffic, port 8080 for Stripe webhook ingress. One Envoy pod serves the whole lab. |
| `shu-billing-lab` | `shu-deploy/kubernetes/local-billing-lab/` | Tenant stack: shu-api, shu-frontend, postgres, redis. Webhook endpoint is internal (reached by the control plane over cluster DNS). |
| `shu-control-plane` | `shu-deploy/kubernetes/control-plane-local/` | Control-plane webhook router: verifies Stripe signatures, looks up the target tenant in its registry, forwards the event under an HMAC envelope. |

Stripe CLI forwards webhooks to `http://localhost:8080/api/v1/billing/webhooks` (the control-plane listener on the shared gateway). Tenants do not receive Stripe traffic directly — the router is the only thing Stripe talks to.

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

- `shu-deploy/` — tenant kustomize overlays + Makefile targets.
- `shu/` — tenant backend source, docs, and this runbook.
- `shu-control-plane/` — control-plane source, Dockerfile, and its own kustomize overlays under `deployment/kubernetes/`.

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

## Populate tenant secrets (shu-billing-lab)

The tenant overlay ships a committed example at `shu-deploy/kubernetes/local-billing-lab/shu-secrets.yaml.example`. Copy it to the real secret file, which is gitignored:

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
| `stripe-customer-id` | Stripe dashboard Part 2.1 |
| `stripe-subscription-id` | Stripe dashboard Part 2.3 |
| `router-shared-secret` | Generated during [Seed the tenant registry](#seed-the-tenant-registry-and-the-router-shared-secret); leave blank for now |
| `openrouter-api-key` | OpenRouter dashboard |
| `mistral-ocr-api-key` | Mistral console |
| `google-client-secret` | Only if you want Google OAuth login; otherwise leave blank |

The router shared secret is generated when you register this tenant in the control-plane registry — come back and populate it there after that step, then restart shu-api. The Stripe webhook secret is no longer needed on the tenant side; the control plane holds it instead.

## Populate control-plane secrets (shu-control-plane)

The control-plane overlay ships a committed example at `shu-deploy/kubernetes/control-plane-local/shu-cp-secrets.yaml.example`. Copy it:

```bash
cd /path/to/shu-workspace/shu-deploy/kubernetes/control-plane-local
cp shu-cp-secrets.yaml.example shu-cp-secrets.yaml
```

The only field that needs a real value is `stripe-webhook-secret` — all database credentials are pre-populated with the lab's shared-Postgres defaults. You'll fill the webhook secret in after the first `stripe listen` run in [Start Stripe CLI webhook forwarding](#start-stripe-cli-webhook-forwarding); for now leave it empty and the control-plane pod will start and crash-loop until you populate it (expected).

| Field | Source |
|---|---|
| `stripe-webhook-secret` | Printed by `stripe listen --forward-to http://localhost:8080/...` the first time you run it |
| All `pg-admin-*`, `cp-db-*`, `database-url*` keys | Pre-populated in the example for the lab's shared-Postgres topology |

## Apply the lab overlays

Before building images locally (first time only):

```bash
# Tenant image
cd /path/to/shu-workspace/shu
docker build -t shu-api:latest -f deployment/docker/api/Dockerfile .

# Control-plane image
cd /path/to/shu-workspace/shu-control-plane
docker build -t shu-control-plane:latest .
```

Then from `shu-deploy/`:

```bash
make apply-lab-full
```

This applies three overlays in order: `shu-gateway/` → `local-billing-lab/` → `control-plane-local/`. It creates:

- Namespace `shu-gateway` with one Envoy Gateway (two listeners on ports 80 and 8080).
- Namespace `shu-billing-lab` with the tenant stack: `postgres`, `redis`, `shu-api`, `shu-frontend`, HTTPRoute attaching to the `:80` listener on the shared gateway, and the tenant migration Job.
- Namespace `shu-control-plane` with the router: `shu-control-plane` deployment, HTTPRoute attaching to the `:8080` listener, plus two lab-only Jobs — `shu-cp-db-init` (creates the `shu_cp` role + database inside the tenant's Postgres) and `shu-cp-migrate` (alembic upgrade head).

You will see one harmless apply-time error from the tenant overlay:

```
error: resource mapping not found for name: "letsencrypt-prod" namespace: "shu-billing-lab" from "kubernetes/local-billing-lab": no matches for kind "ClusterIssuer" in version "cert-manager.io/v1"
```

This is because cert-manager isn't installed on docker-desktop and the `ClusterIssuer` CRD doesn't exist. The lab doesn't use TLS so this is expected. The rest of the apply succeeds despite this error.

Wait for pods across all three namespaces:

```bash
make rollout-status-local-lab
make rollout-status-cp-local
```

Expected final state:

```text
# shu-billing-lab
NAME                            READY   STATUS      RESTARTS   AGE
postgres-...                    1/1     Running     0          ...
redis-...                       1/1     Running     0          ...
shu-api-...                     1/1     Running     0          ...
shu-frontend-...                1/1     Running     0          ...
shu-db-migrate-...              0/1     Completed   0          ...

# shu-control-plane
NAME                            READY   STATUS             RESTARTS   AGE
shu-control-plane-...           1/1     Running            0          ...
shu-cp-db-init-...              0/1     Completed          0          ...
shu-cp-migrate-...              0/1     Completed          0          ...
```

If the control-plane pod is in CrashLoopBackOff with a `SHU_CP_STRIPE_WEBHOOK_SECRET` validation error, you haven't populated it yet — that's fixed in [Start Stripe CLI webhook forwarding](#start-stripe-cli-webhook-forwarding) below. The lab can proceed in this state up to the point where you need a live webhook delivery.

## Run migrations and seeds

Two migration tracks run — the tenant (alembic + hosting deployment seed) and the control plane (alembic against the `shu_cp` database).

### Tenant migrations

The tenant migration Job runs automatically during the first `apply`. It performs both Alembic migrations **and** the hosting deployment seed (creates LLM provider rows, seeds chat/embedding/OCR model configurations).

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

### Control-plane migrations

The control-plane Jobs run automatically during the first `apply-lab-full`: `shu-cp-db-init` creates the `shu_cp` role + database inside the tenant Postgres, and `shu-cp-migrate` runs alembic against it to create the `tenant` table.

If you need to re-run (e.g. after changing the control-plane alembic chain):

```bash
make migrate-cp-local
```

Verify:

```bash
kubectl -n shu-control-plane logs job/shu-cp-migrate | tail -10
```

Expected:

```text
Alembic heads: 0001 (head)
Alembic current: 0001 (head)
Running alembic upgrade head ...
Migrations complete
```

Confirm the table exists:

```bash
kubectl -n shu-billing-lab exec deployment/postgres -- \
  psql -U shu_cp -d shu_cp -c "\d tenant"
```

The table is empty at this point — you'll insert the lab tenant row in the next section.

## Seed the tenant registry and the router shared secret

The control plane needs one row in its `tenant` table before it will forward events to this lab. The row carries a 64-hex HMAC `shared_secret` that must also be configured on the tenant's shu-api. Generate the secret once, insert the registry row, and copy the value to the tenant secret in the same turn.

### 1. Generate the shared secret

```bash
ROUTER_SECRET=$(openssl rand -hex 32)
echo "ROUTER_SECRET=$ROUTER_SECRET"
```

Save the value somewhere ephemeral — you'll use it twice below, then it lives in Kubernetes secrets from that point on.

### 2. Insert the tenant row via port-forwarded psql

In one terminal:

```bash
kubectl -n shu-billing-lab port-forward svc/postgres 5432:5432
```

In another terminal, substitute your `cus_...` id from the Stripe dashboard:

```bash
CUSTOMER_ID=cus_YOUR_STRIPE_CUSTOMER_ID
INSTANCE_URL=http://shu-api.shu-billing-lab.svc.cluster.local/api/v1/billing/webhooks
PGPASSWORD=password psql -h localhost -p 5432 -U shu_cp -d shu_cp <<SQL
INSERT INTO tenant (id, stripe_customer_id, instance_url, shared_secret, status, attributes)
VALUES (gen_random_uuid(), '${CUSTOMER_ID}', '${INSTANCE_URL}', '${ROUTER_SECRET}', 'active', '{}'::jsonb)
ON CONFLICT (stripe_customer_id) DO UPDATE
  SET instance_url = EXCLUDED.instance_url,
      shared_secret = EXCLUDED.shared_secret,
      status = 'active';
SELECT stripe_customer_id, instance_url, status FROM tenant WHERE stripe_customer_id = '${CUSTOMER_ID}';
SQL
```

`ON CONFLICT` keeps the step idempotent — re-running rotates the secret; just re-copy to the tenant (step 3).

### 3. Populate `router-shared-secret` on the tenant and restart shu-api

Base64-encode the same value and paste it into `shu-deploy/kubernetes/local-billing-lab/shu-secrets.yaml` under the `router-shared-secret` key:

```bash
printf '%s' "$ROUTER_SECRET" | base64
# Paste the output into shu-secrets.yaml
```

Re-apply the tenant secret and bounce shu-api so it picks up the new env value:

```bash
cd /path/to/shu-workspace/shu-deploy
make apply-local-lab
kubectl -n shu-billing-lab rollout restart deployment/shu-api
kubectl -n shu-billing-lab rollout status deployment/shu-api --timeout=90s
```

At this point the control plane knows about this tenant and shu-api can verify router envelopes. Smoke-check shu-api didn't start with a 503:

```bash
kubectl -n shu-billing-lab logs deployment/shu-api --tail=20 | grep -i "router_shared_secret\|SHU_ROUTER_SHARED_SECRET" || echo "no errors — router secret is loaded"
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
LOGIN_PAYLOAD='{"email":"you@your-domain.com","password":"YOUR_PASSWORD"}'  # pragma: allowlist secret
curl -sS -X POST http://localhost/api/v1/auth/login/password \
  -H "Content-Type: application/json" \
  -d "$LOGIN_PAYLOAD" \
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

Webhooks enter the control plane on port **8080** (not 80). In a separate terminal:

```bash
stripe listen --forward-to http://localhost:8080/api/v1/billing/webhooks
```

First run prints:

```text
> Ready! You are using Stripe API Version [2026-03-25.dahlia]. Your webhook signing secret is whsec_...
```

Copy that `whsec_...` value — it goes into the **control-plane** secret (not the tenant secret) as `stripe-webhook-secret`. The tenant no longer verifies Stripe signatures; the control plane does.

```bash
printf '%s' 'whsec_...' | base64
# Paste into shu-deploy/kubernetes/control-plane-local/shu-cp-secrets.yaml under stripe-webhook-secret
```

Re-apply the control-plane secret and restart the control-plane deployment:

```bash
cd /path/to/shu-workspace/shu-deploy
make apply-cp-local
kubectl -n shu-control-plane rollout restart deployment/shu-control-plane
kubectl -n shu-control-plane rollout status deployment/shu-control-plane --timeout=90s
```

Leave the `stripe listen` terminal running for the rest of the lab session. The control plane verifies the Stripe signature on every inbound webhook using this secret, then forwards the verbatim event body to the tenant under its HMAC envelope.

## Smoke test the billing pipeline

### 1. First webhook delivery — status transition

Trigger a no-op subscription update on your actual subscription:

```bash
stripe subscriptions update sub_... -d 'metadata[uat]=smoke-1'
```

Within a second or two, the `stripe listen` window should show:

```text
... --> customer.subscription.updated [evt_...]
... <--  [200] POST http://localhost:8080/api/v1/billing/webhooks [evt_...]
```

A 200 return code confirms the end-to-end path: control plane verified the Stripe signature, looked up the lab tenant by customer id, forwarded under HMAC; shu-api verified the HMAC envelope, matched `expected_customer_id`, parsed the event, and the subscription handler ran without raising. The Stripe API version (2026-03-25.dahlia) is pinned in `StripeClient._configure_stripe` on both sides.

Cross-check the control-plane's own log for the outcome:

```bash
kubectl -n shu-control-plane logs deployment/shu-control-plane --tail=20 | grep 'webhook routed'
```

Expected: a line with `"outcome": "forwarded"`, the event id, and `forward_status: 200`.

Confirm the DB state transition:

```bash
kubectl -n shu-billing-lab exec deployment/postgres -- \
  psql -U shu -d shu -c "SELECT subscription_status, quantity, current_period_start, current_period_end FROM billing_state;"
```

Expected: `subscription_status='active'`, `quantity=1`, periods populated.

### 2. Quantity sync

Create a second user via the admin API:

```bash
NEW_USER_PAYLOAD='{"email":"user2@your-domain.com","password":"userpass123!","name":"User Two","role":"regular_user"}'  # pragma: allowlist secret
curl -sS -X POST http://localhost/api/v1/auth/users \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d "$NEW_USER_PAYLOAD"
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
- **SHU-704** — per-seat included-usage allowance via Stripe Credit Grants, plus the asymmetric upgrade-immediate / downgrade-deferred seat policy. Not implemented yet; scenarios 5b/5c and 19 in the SHU-699 matrix will show `known-gap` until SHU-704 ships.
- **SHU-705** — Shu-managed vs BYOK provider provenance for the billing filter. Not implemented yet; scenarios 20/21 will show `known-gap`.

None of these gaps block the lab from being stood up and exercised. They define the ceiling of what you can validate until the respective ticket ships.

## Teardown

Full reset across all three namespaces:

```bash
make delete-lab-full
kubectl -n shu-billing-lab delete pvc --all  # if namespace deletion didn't already clean these
```

`delete-lab-full` tears down control plane → tenant → gateway in reverse of apply order. Lab data (chat history, documents, usage rows) is on `shu-data-pvc` in the tenant namespace and is destroyed with the PVC. The `shu_cp` database lives in the tenant's Postgres and is destroyed with the tenant PVC.

Stripe-side objects (the customer, subscription, events) persist in your Stripe test account independently; manage them from the Stripe dashboard if you need to reset.

## Troubleshooting

### Pod `postgres` CrashLoopBackOff with "PostgreSQL data in /var/lib/postgresql/data (unused mount/volume)"

The paradedb pg18 image requires the PVC mounted at `/var/lib/postgresql` (parent), not `/var/lib/postgresql/data`. Check `common-local/postgres-deployment.yaml` — the `volumeMounts.mountPath` should be `/var/lib/postgresql`. If an older overlay version is in the cluster, re-apply with the current overlay.

### Pod `postgres` ImagePullBackOff for `pgvector/pgvector:latest-pg18`

That tag doesn't exist on Docker Hub. The correct canonical image is `paradedb/paradedb:latest-pg18`, defined in `common-local/postgres-deployment.yaml`. Verify the overlay you're applying has that image.

### `stripe listen` shows `[401]` from the control plane

The 401 comes from the control plane rejecting the Stripe signature (not from shu-api). Two common causes:

1. **`stripe-webhook-secret` was never populated in `shu-cp-secrets.yaml`**, or was populated with stale value. Re-run `stripe listen`, copy the `whsec_...` it prints, base64-encode, update the CP secret, `make apply-cp-local`, restart the CP deployment.
2. **Control-plane pod is using an older secret after rotation.** `stripe listen` caches the webhook secret per-device; if you deleted the CLI state (`rm ~/.config/stripe/`), a new secret is generated on the next run. Always rotate the CP secret alongside.

### Control plane returns `[500]` with outcome `timeout` or `tenant_error`

The control plane reached its registered `instance_url` but shu-api didn't respond. Check:

```bash
kubectl -n shu-control-plane logs deployment/shu-control-plane --tail=20 | grep 'webhook routed'
```

If `outcome: timeout`, shu-api is slow or unreachable. Check it's running (`kubectl -n shu-billing-lab get pods`) and that the `instance_url` in the tenant row matches the actual service DNS (`http://shu-api.shu-billing-lab.svc.cluster.local/api/v1/billing/webhooks`).

If `outcome: tenant_error` with a non-2xx forward status, shu-api responded but rejected. 401 → HMAC verification failed (mismatch between tenant row `shared_secret` and `SHU_ROUTER_SHARED_SECRET`). 409 → `customer_mismatch` (the `stripe_customer_id` on the tenant row doesn't match the tenant's `SHU_STRIPE_CUSTOMER_ID`).

### shu-api `/api/v1/billing/webhooks` returns `[401] signature_invalid`

The router envelope failed verification. Most common cause: the `router-shared-secret` in `shu-secrets.yaml` doesn't match the `shared_secret` column on the tenant's registry row. Re-run the seed step or inspect the mismatch:

```bash
# What shu-api thinks:
kubectl -n shu-billing-lab get secret shu-secrets -o jsonpath='{.data.router-shared-secret}' | base64 -d; echo
# What the registry has:
kubectl -n shu-billing-lab exec deployment/postgres -- \
  psql -U shu_cp -d shu_cp -c "SELECT stripe_customer_id, shared_secret FROM tenant;"
```

They must be identical. If they differ, pick one and copy it everywhere.

### shu-api `/api/v1/billing/webhooks` returns `[503] router_secret_not_configured`

Tenant pod started before `SHU_ROUTER_SHARED_SECRET` was populated. Re-apply the tenant secret and restart:

```bash
make apply-local-lab
kubectl -n shu-billing-lab rollout restart deployment/shu-api
```

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
