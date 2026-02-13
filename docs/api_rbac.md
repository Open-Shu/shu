# Backend API RBAC

_Last updated: 2025-11-20_

This document summarizes backend API endpoints and their effective access control:
- **Public** – no authentication; allowed by `AuthenticationMiddleware.public_paths/public_prefixes`.
- **Authenticated (any user)** – valid JWT or API key required (via middleware / `get_current_user`), but no role constraint.
- **Power user+** – `require_power_user` dependency (power_user or admin).
- **Admin-only** – `require_admin` dependency.
- **KB-gated** – requires per-knowledge-base permissions via `require_kb_*`.
- **Ownership-gated** – requires authenticated user _and_ that the resource belongs to the current user.

Paths are relative to `/api/v1` unless noted.

---

## Global Auth & Public Endpoints

Authentication is enforced by `AuthenticationMiddleware` except for these **public** paths/prefixes:

- `GET /docs`, `GET /redoc`, `GET /openapi.json` – **Public** (FastAPI docs/OpenAPI).
- `GET /health`, `/health/`, `/health/liveness`, `/health/readiness` – **Public**.
- `GET /config/public` – **Public** app config.
- `POST /auth/login`, `POST /auth/register`, `POST /auth/login/password`, `POST /auth/refresh` – **Public** auth flows.
- `GET /auth/google/login`, `POST /auth/google/exchange-login` – **Public** Google auth entrypoints.
- `GET /host/auth/callback` (under `/api/v1`) – **Public** OAuth callback.
- `GET /auth/callback` (no `/api/v1` prefix) – **Public** alias callback.
- `GET /settings/branding`, `GET /settings/branding/` – **Public** branding metadata.
- `GET /settings/branding/assets/{filename}` (via public prefix) – **Public** branding assets.

All other endpoints require an `Authorization` header (JWT or `ApiKey`) and then route-level RBAC applies.

---

## Auth Router (`/auth`)

- `POST /auth/login`, `/register`, `/login/password`, `/refresh` – **Public** (see above).
- `GET /auth/me` – **Authenticated (any user)** via `get_current_user`.
- `GET /auth/users` – **Admin-only** (`require_admin`).
- `GET /auth/users/{user_id}` – **Admin-only`.
- `POST /auth/users/{user_id}/activate`, `/deactivate`, `/delete` – **Admin-only`.

Google auth endpoints are public via middleware, even though no explicit dependency is set.

---

## Health & System Routers (`/health`, `/system`)

- `GET /health`, `/health/`, `/health/liveness`, `/health/readiness` – **Public**.
- `GET /health/database` – **Authenticated (any user)** (middleware only; no explicit `get_current_user`).
- `GET /system/version` – **Authenticated (any user)** (middleware only; no explicit `get_current_user`).

---

## Config & Branding Routers (`/config`, `/settings/branding`)

- `GET /config/public` – **Public** app config for frontend.
- `GET /settings/branding`, `/settings/branding/` – **Public** branding settings.
- `GET /settings/branding/assets/{filename}` – **Public** branding assets (prefix-based).
- `PATCH /settings/branding` – **Admin-only** (`require_admin`).
- `POST /settings/branding/favicon` – **Admin-only**.

---

## Knowledge Bases Router (`/knowledge-bases`)

Role/KB-gating is mixed; key patterns:

- `GET /knowledge-bases` – **Power user+** (`require_power_user`).
- `GET /knowledge-bases/stats` – **Admin-only**.
- `POST /knowledge-bases` – **Power user+**.
- `GET /knowledge-bases/{kb_id}` – **Power user+** _and_ **KB Query (default)** (`require_kb_query_default`).
- `PUT /knowledge-bases/{kb_id}` – **Power user+** _and_ **KB Manage (default)** (`require_kb_manage_default`).
- `DELETE /knowledge-bases/{kb_id}` – **Power user+** _and_ **KB Delete (default)** (`require_kb_delete_default`).
- `POST /knowledge-bases/{kb_id}/status`, `PUT /{kb_id}/rag-config` – **KB Manage (default)**.
- `GET /knowledge-bases/{kb_id}/summary`, `/rag-config`, `/documents`, `/documents/{document_id}`, `/documents/{document_id}/chunks`, `/documents/extraction-summary` – **KB Query (default)**.
- `GET /knowledge-bases/by-source-type/{source_type}` – **Authenticated (any user)** (`get_current_user`).
- `GET /knowledge-bases/rag-config/templates` – **Authenticated (any user)**.
- `GET /knowledge-bases/{kb_id}/validate` – **Authenticated (any user)** (middleware only; **no KB guard**).

---

## KB Permissions Router (`/permissions`)

- `POST /permissions/{kb_id}/permissions` – **KB Manage (access)** (`require_kb_manage_access("kb_id")`).
- `GET /permissions/{kb_id}/permissions` – **KB Manage (access)**.
- `DELETE /permissions/{kb_id}/permissions/{permission_id}` – **KB Manage (access)**.
- `GET /permissions/{kb_id}/permissions/effective` – **Authenticated (any user)**; service enforces:
  - Caller can always see their own effective permissions.
  - Caller can see others only if they can manage the KB or are admin.

---

## Groups Router (`/groups`)

All endpoints under `/groups` use `require_admin`:
- Group CRUD and membership management – **Admin-only**.

---

## Resources Router (`/resources`)

- `GET /resources/stats` – **Admin-only**.
- `POST /resources/cleanup` – **Admin-only**.
- `POST /resources/clear-cache` – **Admin-only**.
- `GET /resources/health` – **Authenticated (any user)** (`get_current_user`).

---

## LLM Router (`/llm`)

- `GET /llm/models` – **Authenticated (any user)** (`get_current_user`).
- `GET /llm/health` – **Authenticated (any user)**.
- All provider CRUD, provider tests, model CRUD/sync/disable, and provider-type definition endpoints – **Admin-only** (`require_admin`).

---

## Model Configuration Router (`/model-configurations`)

Core model configuration management:
- `POST /model-configurations` – **Power user+**.
- `GET /model-configurations` – **Authenticated (regular user+)**; regular users are restricted to active configs only.
- `GET /model-configurations/{config_id}` – **Authenticated (regular user+)**; regular users may only retrieve active configs.
- `PUT /model-configurations/{config_id}` – **Power user+`.
- `DELETE /model-configurations/{config_id}` – **Power user+`.
- `POST /model-configurations/{config_id}/test` – **Power user+**.

KB prompt assignment endpoints:
- `GET /model-configurations/{config_id}/kb-prompts` – **Power user+`.
- `POST /model-configurations/{config_id}/kb-prompts` – **Authenticated (any user)** (`get_current_user`).
- `DELETE /model-configurations/{config_id}/kb-prompts/{knowledge_base_id}` – **Authenticated (any user)**.

---

## Prompts Router (`/prompts`)

All prompt CRUD and assignment endpoints use `require_power_user`:
- `/prompts*` – **Power user+**.

---

## Query Router (`/query`)

All endpoints here require KB query access via `require_kb_query_access`:
- `POST /query/{knowledge_base_id}/search` – **KB Query (access)**.
- `GET /query/{knowledge_base_id}/documents` – **KB Query (access)**.
- `GET /query/{knowledge_base_id}/documents/{document_id}` – **KB Query (access)**.
- `GET /query/{knowledge_base_id}/stats` – **KB Query (access)**.

---

## Chat & Conversations Router (`/chat`)

All chat endpoints use `get_current_user` and enforce conversation ownership where applicable:

- Conversation CRUD (`/chat/conversations`, `/conversations/{conversation_id}`) – **Authenticated + Ownership-gated** (only own conversations).
- Messages (`/chat/conversations/{conversation_id}/messages`) – **Authenticated + Ownership-gated**.
- Send/stream/regenerate/switch-model (`/chat/conversations/{conversation_id}/send|stream|regenerate|switch-model`) – **Authenticated + Ownership-gated**.
- Attachments upload (`POST /chat/conversations/{conversation_id}/attachments`) – **Authenticated + Ownership-gated**.
- Attachment retrieval (`GET /chat/attachments/{attachment_id}`) – **Authenticated (any user)** with internal checks to ensure access only to own attachments.

No admin/power_user differentiation inside chat; constraints are per-user ownership.

---

## Chat Plugins & User Preferences Routers

**Chat plugins (`/chat-plugins`):**
- `GET /chat-plugins` – **Authenticated (any user)**; list plugins available to the user, honoring subscriptions & scopes.
- `POST /chat-plugins/execute` – **Authenticated (any user)**; execution further constrained by plugin-level checks.

**User preferences (`/user/preferences`):**
- `GET /user/preferences`, `PUT /user/preferences`, `PATCH /user/preferences` – **Authenticated (any user)** (current user only by implementation).

---

## Plugins (User-Facing) & Plugins Router (`/plugins`)

**User-facing plugins (`plugins_public`):**
- `GET /plugins/` – **Authenticated (any user)**; list plugins.
- `GET /plugins/{name}` – **Authenticated (any user)**; plugin details.
- `POST /plugins/{name}/execute` – **Authenticated (any user)**; execution constrained by subscriptions/scopes.

**Plugins router (`plugins_router`):**
- `GET /plugins` (no trailing slash) – **Authenticated (any user)**; alias for list plugins.

---

## Plugin Admin Routers (Executions, Secrets, Admin, Feeds)

All of these routers consistently use `require_power_user`:

- `/admin/executions*` & `/admin/scheduler/metrics` (plugin executions) – **Power user+**.
- `/admin/{name}/secrets*` (plugin secrets) – **Power user+**.
- Plugin admin endpoints (`/plugins/admin/*` – enable/disable, upload, sync, limits, schemas, etc.) – **Power user+**.
- Plugin feed schedule/job endpoints (`/plugins/feeds/*`) – **Power user+**.

Admins also satisfy `require_power_user`.

---

## Host Auth Router (`/host/auth`)

Excluding the **public callbacks** listed earlier, all host-auth endpoints use `get_current_user`:

- Status, authorize, consent-scopes – **Authenticated (any user)**.
- Subscriptions CRUD – **Authenticated (any user)** (for the current user) with additional internal checks.
- Token exchange & disconnect – **Authenticated (any user)**.
- Delegation/service-account checks – **Authenticated (any user)**.

---

## Groups, User Permissions, Agents, Side-call

**Groups (`/groups`):**
- All endpoints – **Admin-only** (`require_admin`).

**User permissions (`/user-permissions`):**
- All endpoints, including `/users/me/*` – **Admin-only** (`require_admin`).
  - Note: Docstrings imply self-service for `/me` endpoints, but current implementation is admin-gated.

**Agents (`/agents`):**
- `POST /agents/morning-briefing/run` – **Authenticated (any user)** (`get_current_user`), runs for the calling user.

**Side-call (`/side-call`):**
- Side-call configuration endpoints – **Admin-only**.
- Conversation-level endpoints (`/summary/{conversation_id}`, `/auto-rename/{conversation_id}`, `/auto-rename/{conversation_id}/unlock`) – **Authenticated + Ownership-gated** (current user must own the conversation).
