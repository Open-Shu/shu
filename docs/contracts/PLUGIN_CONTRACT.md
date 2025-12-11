# PLUGIN_CONTRACT

Implementation Status: Partial


> Terminology: The platform standardizes on 'plugin' instead of 'tool'. This document was renamed from TOOL_CONTRACT.md; all terminology and routes refer to plugins. Where legacy names remain in older examples, read them as plugins.

## Limitations / Known Issues
- Schema may evolve with initial plugin conversions
- v1 will supercede the MVP v0 implementation (src/shu/agent/*). We will not maintain dual support; MVP paths will be removed once v1 plugins and orchestration are migrated and green in integration tests.



## Purpose
Define the standardized interface and result schema for plugins used by agents and workflows.


### Scope of Schemas
Parameter and output schemas are first‑class contracts in Shu. They serve multiple purposes beyond UI form rendering:
- Power admin execution UIs and (later) chat plugin usage by enabling schema→form generation and validation
- Enable autonomous agents to plan, validate, and chain plugin calls deterministically
- Provide typing for knowledge objects and indexing pipelines
- Support test harnesses and contract validation between plugins and host
Do not treat schemas as UI‑only; they are the foundation for plugin interoperability and knowledge across the platform.

## Base Interface (Conceptual)
- BaseTool
  - execute(parameters: JSON, user_context)
  - get_schema(): JSON Schema for parameters
  - get_output_schema(): JSON Schema for ToolResult.data when status == "success" (recommended)
  - requires_permission(): permission key(s)

## Result Contract
- ToolResult
  - status: success|error|timeout
  - data: JSON
  - error: {code, message}
  - cost: {tokens?, api_calls?}
  - diagnostics?: [string]
  - skips?: [{ id, name?, reason, details? }]  // recommended for ingestion-style ops
  - citations?: [{type, ref, label}]

Host behavior: When a plugin provides an output schema, the host validates ToolResult.data against it. Violations return HTTP 500 with error=output_validation_error.

Guidance: diagnostics should be reserved for non-error, developer-facing details. Hosts SHOULD gate verbose diagnostics behind either a per-call parameter `debug=true` or an environment flag (e.g., `settings.DEBUG`). For user-visible operational outcomes (like “skipped 1 file”), plugins SHOULD surface concise reasons as human-readable strings in `diagnostics` (or, preferably, as structured entries in `skips`).

Recommended structure for ingestion “skips”:

- skips[] items:
  - id: provider’s external id for the item (required)
  - name: display name/title (optional)
  - reason: one of [too_large, ext_filtered, unsupported_format, empty_extraction, auth, network, other]
  - details: free-form object with contextual fields (e.g., size, max, ext, allowed, mime, content_type)

This allows admin UIs to render accurate summaries without parsing free text, while keeping backward compatibility via the `diagnostics` array.

## Example: Execute Request
```json
POST /api/v1/plugins/web_search/execute
{
  "parameters": {"query": "status of project shu"},
  "user_context": {"user_id": "uuid", "permissions": ["plugin:web_search"]}
}
```

## Example: Execute Response
```json
{
  "status": "success",
  "data": {
    "results": [
      {"title": "Shu Update", "url": "https://...", "snippet": "..."}
    ]
  },
  "cost": {"api_calls": 1},
  "citations": [{"type": "web", "ref": "https://...", "label": "Shu Update"}]
}
```

## Security
- Tools must declare required permissions; enforced by RBAC prior to execution
- Audit all executions with params hash and result status



## Plugin Packaging and Discovery (Python-only)
- Requirement: All plugins are Python packages. No IPC/containers required.
- Discovery: Host loads plugins via importlib using a manifest-provided entrypoint path.
- Manifest (Python dict or YAML) must declare:
  - name, version
  - entrypoints: { plugin_name: "package.module:ClassName" }
  - capabilities (optional): ["cache","identity","secrets","http","auth","kb","rate_limit","scheduler","storage","cursor"]
    - Note: 'cursor' is auto-included when 'kb' is declared; declare 'cursor' explicitly only if you need cursors without 'kb'.
  - secrets (optional): ["google_service_account_json", ...]
- Capabilities are optional. Plugins may implement equivalent functionality internally. When falling back, they should emit a diagnostic note describing reduced fidelity or higher cost.


### Packaging Standard
- Distribution: source package zip containing a manifest and Python package folder(s). Optional vendor/ for bundled pure-Python deps.
- Example layout:

```
my_gchat_plugin.zip
  ├── manifest.yaml
  ├── my_gchat_plugin/
  │   ├── __init__.py
  │   ├── plugin.py   # contains class Plugin(ToolPlugin)
  │   └── util.py
  └── vendor/
      └── some_dep/
          ├── __init__.py
          └── ...
```

- Manifest examples:

Python dict (manifest.py):
```python
PLUGIN_MANIFEST = {
  "name": "gchat_digest",
  "version": "1.0.0",
  "entrypoints": {"gchat_digest": "my_gchat_plugin.plugin:Plugin"},
  "capabilities": ["cache", "identity", "secrets", "http"],
  "secrets": ["google_service_account_json"],
}
```

YAML (manifest.yaml):
```yaml
name: gchat_digest
version: 1.0.0
entrypoints:
  gchat_digest: my_gchat_plugin.plugin:Plugin
capabilities: [cache, identity, secrets, http]
secrets: [google_service_account_json]
```

- Host behavior:
  - Unzips into plugins/; adds vendor/ to sys.path for this plugin only; loads entrypoint via importlib.
  - If isolated envs are enabled by an admin policy, host may provision a per-plugin venv and install requirements.txt (optional; off by default).


### Install Policy
- Default: self-contained plugins. Use vendor/ for pure-Python deps; no external installs at load time.
- Optional (admin-enabled): per-plugin virtualenv with requirements.txt installation. Off by default; requires network access and policy approval.
- Isolation: host prepends vendor/ to the plugin-specific sys.path. If venv is used, imports resolve inside that venv for the plugin.
- Security: untrusted plugins should avoid host.http; secrets access is capability-gated.

### Source Sync Pattern
- Initial load may be bulk; subsequent syncs SHOULD be delta-based using provider watermarks (e.g., historyId, syncToken, updated_at).
- Plugins SHOULD expose next_cursor and honor idempotency keys; host provides per-source watermarks in context.
- Store raw artifacts and parsed records with content hashes for dedupe; include lineage to the source object and provider ids.
- Host indexer consumes stored artifacts to update KB and embeddings; agents/workflows operate primarily on indexed data.
- Briefings and decisions read indexed data by default; live reads are optional freshness checks.

## Execute API (Read-only)
- Interface (conceptual): `async def execute(self, params: dict, context: dict, host) -> ToolResult`
- Streaming (optional): Return an async iterator yielding progress/content events; host converts to SSE for UI.
- Result schema (extends ToolResult above):
  - status: success|error|timeout
  - data: JSON (plugin-defined)
  - diagnostics?: [string]
  - next_cursor?: string (optional continuation)


Guidance: diagnostics should be reserved for non-error, developer-facing details. Hosts SHOULD gate verbose diagnostics behind either a per-call parameter `debug=true` or an environment flag (e.g., `settings.DEBUG`).

## Actions API (Write operations)
- Purpose: Two-way operations (e.g., mark email read, draft reply, send message) with preview/approval.
- Discovery: `async def list_actions(self) -> list[ActionMeta]`
  - Each ActionMeta includes: name, description, params_schema, required_scopes, requires_approval: bool
- Execution: `async def perform_action(self, name: str, args: dict, context: dict, host) -> ActionResult`
  - Must support idempotency via a host-provided idempotency_key in context
  - May return:
    - { status: "pending_approval", action_id, preview, diagnostics? }
    - { status: "ok", action_id, result, diagnostics? }
    - { status: "error", action_id?, error }
  - Side-effects should only occur after approval when requires_approval is true
- Audit: Host must log subject, plugin/action, params hash, decision, approver (if any), timestamps

## Capabilities (Optional, Python interfaces)
- cache.get/set(key, value, ttl?)
- identity.lookup(kind, value)  // e.g., kind="google_directory"; recommended but optional
- secrets.get(name)
- http.fetch(request, retries/backoff)
- rate.limit(bucket, cost)
- scheduler.enqueue(task, run_at)  // only if background capability is granted
- storage.put/get(reference)
Note: Plugins may ignore capabilities and implement equivalents internally. Host cannot enforce centralized policy if the plugin bypasses capabilities; use RBAC and sandboxing accordingly.


## Plugin Independence Policy
- Plugins must not import host-internal modules (e.g., `shu.*`). The loader SHOULD enforce this with an import guard at sync/load time; violations fail plugin load with a clear error.
- Egress policy: Plugins must not import direct HTTP clients (requests, httpx, urllib3, urllib.request). All network egress goes through `host.http` for auditing and policy enforcement. Enforcement is performed via static scan + runtime import hook + capability whitelist.
- Plugins declare required capabilities in the manifest. At runtime, the executor passes a `host` object exposing only those capabilities (e.g., `host.cache`, `host.storage`, `host.secrets`, `host.http`, `host.scheduler`, `host.query`, `host.logger`, `host.audit`, `host.identity`).
- No direct DB model imports, ORM sessions, or service-class coupling from plugins. Read/write occurs only through declared host capabilities.
- Multi-type plugins are allowed: a single plugin can implement ingestion, transform/digest, and action contracts.
- Identity mapping: Plugins SHOULD obtain user/org-specific addresses (e.g., Google email) via `host.identity` when available, or accept parameters with defaults populated from identity.
- Result: Plugins are portable and can run on any host that implements the same capability interface.


### OCR Policy (Feeds)
Implementation Status: Partial

Limitations/Known Issues:
- Some plugin paths only use extracted text and do not persist extraction metadata yet.
- Per-feed OCR mode is set via a reserved host-only overlay and is not part of the plugin parameter schema.

Policy:
- OCR policy is enforced by host.kb ingestion. Plugins SHOULD NOT implement their own OCR policy.
- `host.ocr` is a utility for direct extraction when needed and does not implicitly enforce per-feed policy.
- The host supports a 4-tier OCR mode: `always | auto | fallback | never`.
  - always: force OCR
  - auto: OCR PDFs/images; direct extraction for text-born formats
  - fallback: try non-OCR first; if no text, retry with OCR
  - never: do not OCR

Configuration (UI/feeds):
- The Feed Create/Edit dialogs write the per-feed selection to `params.__host.ocr.mode`.
- The executor strips `__host` before plugin validation/execution and passes it to `make_host(host_context=...)`.
- `make_host` passes OCR mode into `host.kb`; `host.ocr` is optional and mode-less by default.

Return value:
- `host.ocr.extract_text(...)` returns the full extractor result including metadata, e.g.:
  - `{ "text": "...", "metadata": { "method": "ocr|text|pdf_text", "engine": "easyocr|pymupdf|...", "confidence": 0.80, "duration": 13.79, "details": { ... } } }`
- Callers that only need text may read `result.text`; callers that surface extraction details SHOULD persist `metadata` in their document model.

Security/DRY:
- Centralizing OCR in the host keeps plugins simpler and allows consistent policy without duplicating logic across plugins.



### host.kb Ingestion APIs (type-specific and catch-all)
Implementation Status: Partial

Purpose: Provide plugin-friendly, high-level ingestion calls that centralize extraction (OCR/text), metadata persistence, dedupe, chunking, and embeddings. Plugins avoid free-handing KO construction.

Methods (async):
- host.kb.ingest_document(kb_id, *, file_bytes, filename, mime_type, source_id, source_url?, attributes?) -> { ko_id, document_id, word_count, character_count, chunk_count, extraction }
- host.kb.ingest_email(kb_id, *, subject, sender?, recipients: {to, cc, bcc}, date?, message_id, thread_id?, body_text?, body_html?, labels?, source_url?, attributes?) -> {...}
- host.kb.ingest_thread(kb_id, *, title, content, thread_id, source_url?, attributes?) -> {...}
- host.kb.ingest_text(kb_id, *, title, content, source_id, source_url?, attributes?) -> {...}
- host.kb.ingest(kb_id, **kwargs) -> {...}  // catch-all that routes based on provided fields; prefer specific methods

Behavior:
- Document: Applies per-feed OCR mode (always|auto|fallback|never); extracts text via TextExtractor; persists full extraction metadata (method, engine, confidence, duration, details)
- Email: Builds an indexable textual representation from structured inputs; extraction_method=text; preserves message_id/thread_id/labels in extraction_metadata
- Thread: Treats conversation threads as text artifacts; caller provides content; extraction_method=text
- Text: Minimal ingestion for raw text; extraction_method=text

Attributes:
- Optional dict. Keys used today:
  - source_url, source_hash, modified_at (ISO8601 or RFC3339)
  - extraction_metadata (merged into host-populated details where relevant)
  - external_id (ingest_email only; overrides stored source_id for dedupe)
  - Additional keys are accepted and may be persisted in extraction_metadata

Return envelope (all methods):
- ko_id: deterministic id derived from plugin + source/thread/message id
- document_id
- word_count, character_count, chunk_count
- extraction: { method, engine, confidence, duration, details }

Idempotency and dedupe:
- Upsert keyed by (kb_id, source_id) for all helper types. `ingest_email` defaults `source_id` to the supplied `message_id`; plugins may override via `attributes.external_id` if they need a different dedupe key.
- content_hash is computed and stored; chunking fully replaces existing chunks on update

Policy integration:
- OCR mode is read from host_context.__host.ocr.mode and applied automatically; plugins do not pass policy knobs in params

Security:
- Callers are responsible for PII/redaction prior to ingestion

Examples:
- Drive: await host.kb.ingest_document(kb_id, file_bytes=blob, filename=name, mime_type=mt, source_id=file_id, source_url=webViewLink)
- Gmail: await host.kb.ingest_email(kb_id, subject, sender, recipients, date, message_id, thread_id, body_text, labels)

### Origins (source_type) for plugin-written documents
- When plugins write documents/knowledge, the host records origin using `source_type = "plugin:<plugin_name>"`.
- `plugin:`-prefixed values are reserved for plugin-defined origins and are not validated against legacy SourceType registries.
- Manifests MAY declare explicit origins. The loader MAY register these for API/UI enumeration while the persisted `source_type` string remains stable for compatibility.
- Rationale: preserve plugin independence and avoid coupling integrations to pre-seeded host source types.

### Error Surfaces: Output Validation
- When a plugin declares `get_output_schema()`, the host validates `ToolResult.data` against it.
- On violation, the host returns HTTP 500 with an error envelope. Example:
```json
{
  "error": {
    "code": "HTTP_500",
    "message": { "error": "output_validation_error", "message": "<jsonschema message>" },
    "details": {},
    "error_id": "ERR-..."
  }
}
```
- The `message` may be an object containing `{ error, message }` to preserve structure. UI should render a concise summary and allow toggling raw details.

### Plugin Types (bring-your-own integrations)
- Ingestion Plugins: connect to external platforms/APIs to fetch/sync data. Use `host.http`, `host.secrets`, `host.cursor` (for incremental cursors), and optional `host.scheduler`. No dependence on host processors.
- Digest/Transform Plugins: read normalized or raw data via `host.query` or by fetching directly via `host.http`, then transform to summaries/insights.
- Action Plugins: perform write operations with preview/approval patterns using host RBAC/audit; respect `list_actions`/`perform_action` contract.
- Hosts MAY provide a cross-platform ingestion substrate, but plugins MUST remain able to bring their own ingestion logic entirely.

### Feeds: Declaring feed-safe operations (Implementation Status: Partial)
- Purpose: Background schedules ("Feeds") must only execute operations that are safe unattended (ingestion/transform without external side-effects).
- Manifest keys (optional, per plugin):
  - `default_feed_op`: string — op to use when scheduling if caller omits `op`
  - `allowed_feed_ops`: [string] — whitelist of ops permitted in Feeds
- Host enforcement:
  - On schedule create/update, if `allowed_feed_ops` is non-empty:
    - If `op` is missing and `default_feed_op` exists → host injects it
    - If `op` is missing and no default → 400 invalid_feed_op
    - If `op` is present but not in the whitelist → 400 invalid_feed_op
- UI behavior:
  - Feed creation/edit reads these manifest keys; when only one allowed op exists, the `op` field is hidden and locked.
- Example (gmail_digest):
  ```python
  PLUGIN_MANIFEST = {
    "name": "gmail_digest",
    ...,
    "default_feed_op": "digest",
    "allowed_feed_ops": ["digest"],
  }
  ```
- Known Issues:
  - Not all plugins declare these keys yet; when absent, no additional enforcement occurs.

## RBAC for Actions
- Policy model: subject (user, groups, roles) × target (plugin, action[, resource attrs]) → decision: allow | deny | allow_with_approval
- Enforcement points:
  - list_actions: filter out actions the subject cannot see/use
  - perform_action: enforce decision; if allow_with_approval, return pending_approval
- Deny-by-default unless explicit allow/approval exists; Deny wins over broad allows
- Idempotency required; approval tokens must be unguessable; all events audited

## Limitations / Known Issues
- Identity formats vary by provider; identity.lookup recommended for consistency
- Streaming event schema may evolve; keep events small and frequent
- Background capabilities (schedules/webhooks) are out of scope unless explicitly granted
- Security review needed before enabling host.http for external calls in untrusted plugins



## Schema UI Hints (for Shu UI)
Implementation Status: Partial

- x-ui.hidden / x_ui.hidden
  - Boolean hint on a field to hide it from forms. The value may still be provided programmatically (e.g., host binding), but the UI will not render an input.
- x-ui.help / x_ui.help
  - String help text shown as a tooltip (question-mark icon) next to the field label or as helper text under the control. Use concise sentences to explain meaning/impact of the parameter.
- x-binding
  - Schema-level hint telling the host/UI to auto-populate a value from context. Common values:
    - identity:<key> (e.g., identity:google_email) → resolves from host.identity
    - secret:<name> → resolves from host.secrets
    - env:<NAME> → resolves from environment
- x-ui.show_when
  - Conditional visibility for a field based on other field values. Supported shapes:
    - { field: "auth_mode", equals: "domain_delegate" }
    - { field: "mode", in: ["a", "b"] }
    - { auth_mode: "domain_delegate" } (map form; all entries must match)
  - If the condition evaluates false, the field is not rendered.
- x-ui.enum_labels
  - Map enum values to human-readable labels in selects
- x-ui.placeholder
  - Placeholder/label for the implicit "Auto" option when the field allows null

- x-ui.enum_help
  - Map enum values to per-option help text for selects. The UI shows these as option tooltips and displays the selected option's help as helper text under the control.

Example (per-option labels and help):
```json
{
  "op": {
    "type": "string",
    "enum": ["ingest", "digest"],
    "x-ui": {
      "enum_labels": {"ingest": "Ingest full content", "digest": "Create summary"},
      "enum_help": {"ingest": "Pulls full items into the Knowledge Base.", "digest": "Summarizes into a single Knowledge Object."}
    }
  }
}
```

Example (no auth fields in params):
```json
{
  "since_hours": {"type": "integer", "default": 48, "x-ui": {"help": "Look-back window in hours"}},
  "query": {"type": ["string","null"], "x-ui": {"help": "Optional filter"}}
}
```

### Capability-driven Auth Declaration (AUTH-REF-001)
Implementation Status: Partial

- Plugins MUST NOT include auth fields in parameter schemas (e.g., `auth_mode`, `user_email`, `impersonate_email`). These are removed in v1.
- Instead, plugins declare auth needs in the manifest (global and/or per-op):
  - `required_identities` (global): list of identity requirements
    - Example item: { provider: "google", mode: "user", scopes: ["..."], optional?: false }
  - `op_auth` (per-op, optional): { provider: "google", mode: "user"|"service_account"|"domain_delegate", scopes: ["..."], subject_hint?: "identity:google_email", secrets?: {...} }
- Host resolves tokens via `host.auth` provider registry and injects them; UI derives prompts (connect/test) from these declarations.
- For impersonation (domain delegation), `subject_hint` can reference identity bindings (e.g., `identity:google_email`). Subject entry remains a UI prompt only when derivation is not possible.

### op_auth.secrets - Secret Requirements

Plugins can declare secret (API key) requirements per-op via `op_auth[op].secrets`:

```json
{
  "op_auth": {
    "my_operation": {
      "secrets": {
        "api_key": { "allowed_scope": "system_or_user" },
        "user_token": { "allowed_scope": "user" }
      }
    }
  }
}
```

Secret scope values:
- `"user"`: Secret must be configured by the user (per-user override only).
- `"system"`: Secret must be configured by admin (system-wide shared secret only).
- `"system_or_user"` (default): User secret preferred, system secret as fallback.

At execution time, the host enforces that all declared secrets are available per their allowed_scope. If a required secret is missing, execution fails with a `missing_secrets` error.

Secrets are accessed at runtime via `host.secrets.get(key)`, which implements user->system fallback for reads.

Provider adapter API (host.auth):
- resolve_token_and_target(provider: str) -> (Optional[str], Optional[str])  // host uses op_auth scopes and UI selection
  - Returns (access_token, target)
  - For Google user mode: target = concrete account email (preferred). If unavailable, target = "me".
  - For domain_delegate: target = impersonation subject email.
  - For service_account: target = null (plugins may ignore or use provider-specific defaults).
- provider_user_token(provider: str, required_scopes?: List[str]) -> Optional[str]
- provider_service_account_token(provider: str, scopes: List[str], subject?: str) -> str

UI policy:
- IdentityGate renders Connect/Test buttons based on `required_identities`.
- Per-op declarations determine if additional prompts (e.g., subject) are needed.
- Plugin Subscriptions page (/settings/connected-accounts) displays:
  - Provider connection status and subscription toggles per plugin
  - Plugin secret requirements from op_auth[op].secrets, with UI for users to configure their secrets

Security/SoC:
- Centralize token acquisition in `host.auth` using provider adapters; do not embed provider SDKs in plugins.
- RBAC/approval policies for write operations are handled by host; plugin metadata indicates risk levels and required scopes.
- Secrets are encrypted at rest and never returned in plaintext via API; only key presence/metadata is exposed.
