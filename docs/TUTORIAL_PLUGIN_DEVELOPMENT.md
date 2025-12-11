## Shu Plugins: Step‑by‑Step Tutorial (Beginner Friendly)

Implementation Status: Complete (tutorial); references current code paths
Limitations/Known Issues: Focuses on Plugins v1 contract; does not cover legacy code paths
Security Vulnerabilities: None in this document; follow capability and secret guidance below

### What you’ll build
- A simple plugin that lists Gmail emails, can perform safe actions with preview/approval, and (optionally) writes a digest into the Knowledge Base (KB).
- You’ll learn how to declare capabilities and call them via the HostCapabilities interface.

### Concepts (1 minute)
- Plugin: A self‑contained module living under ./plugins that exposes a class with execute(params, context, host) and JSON schemas for input/output.
- HostCapabilities: The only way a plugin talks to the host app. You must declare each capability you plan to use in your manifest; undeclared capabilities are blocked at runtime.
- Capabilities available now:
  - http: Make outbound HTTP requests via centralized client with audit and policy
  - identity: Get current user identity (user_id, user_email)
  - auth: OAuth helpers (generic + Google service account/JWT) and token refresh
  - secrets: Encrypted per‑user/per‑plugin key‑value store for credentials
  - storage: Small JSON KV per user+plugin for non-sensitive misc state
  - kb: Upsert Knowledge Objects (documents) into a specified Knowledge Base
  - cache: Short‑lived per‑plugin cache (TTL)
  - ocr: Text extraction/OCR via host capability (policy enforced by host.kb)
  - Note: cursor is auto‑included when kb is declared; declare 'cursor' only if you need cursors without kb

### File layout (2 minutes)
Create a new folder under `plugins/your_plugin_name` with two files:
1) manifest.py — declarative manifest
2) plugin.py — your plugin implementation

Example manifest:
<augment_code_snippet mode="EXCERPT">
````python
PLUGIN_MANIFEST = {
    "name": "my_plugin",
    "display_name": "My Plugin",  # Human-readable name shown in UI
    "version": "1",
    "module": "plugins.my_plugin.plugin:MyPlugin",
    "capabilities": ["http", "identity", "auth", "secrets", "storage", "kb"],  # cursor auto-included with kb
    "default_feed_op": "ingest",  # Default operation for background feeds
    "allowed_feed_ops": ["ingest"],  # Operations safe for unattended execution
    "chat_callable_ops": ["list"],  # Operations safe for chat (read-only, no side effects)
    # Optional: connected-account requirements that drive UI connect prompts
    "required_identities": [
        {
            "provider": "google",
            "mode": "user",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"]
        }
    ],
    # Optional but recommended when using host.auth; enforced per op
    "op_auth": {
        "list": {
            "provider": "google",
            "mode": "user",
            "allowed_modes": ["user", "domain_delegate"],
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
            "subject_hint": "identity:google_email"
        }
    }
}
````
</augment_code_snippet>

Key fields:
- name: Unique plugin name (becomes /plugins/{name}); must match class name
- display_name: Human-readable title shown in UI (optional; falls back to name)
- version: String, informational for now
- module: Dotted path to the class implementing the plugin
- capabilities: Exact list; your plugin will only get these host.* attributes
- default_feed_op: Operation to use when scheduling if caller omits `op` (optional)
- allowed_feed_ops: Whitelist of ops permitted in background Feeds (optional; enforced on create/update)
- chat_callable_ops: Whitelist of ops safe for chat invocation (optional; read-only ops only)
- required_identities: Drives “connect account” UX and host.auth token resolution; each entry specifies provider/mode/scopes
- op_auth: Per-op auth contract (provider, allowed_modes, scopes, subject_hint) for host.auth helpers

### Minimal plugin skeleton (5 minutes)
<augment_code_snippet mode="EXCERPT">
````python
from typing import Any, Dict, Optional
from shu.plugins.base import ExecuteContext, PluginResult

class MyPlugin:
    name = "my_plugin"  # Must match manifest name
    version = "1"

    def get_schema(self) -> Optional[Dict[str, Any]]:
        # REQUIRED: Must include 'op' field with at least one enum value
        return {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["list", "search"], "default": "list"},
                "q": {"type": ["string", "null"]}
            }
        }

    def get_output_schema(self) -> Optional[Dict[str, Any]]:
        # Optional but recommended; host validates output against this schema
        return {"type": "object", "properties": {"result": {"type": ["object", "null"]}}}

    async def execute(self, params: Dict[str, Any], context: ExecuteContext, host: Any) -> PluginResult:
        op = params.get("op", "list")
        # Access host.identity if declared
        user_email = getattr(getattr(host, "identity", None), "user_email", None)
        # Make an HTTP request if you declared http
        # resp = await host.http.fetch("GET", "https://api.example.com", params={"q": params.get("q")})
        return PluginResult.ok({"hello": user_email, "op": op})
````
</augment_code_snippet>

Notes:
- Keep the execute signature (params, context, host). context is reserved for future host-provided metadata.
- **REQUIRED**: Your schema MUST include an `op` field with at least one enum value. The loader enforces this at sync time.
- Plugin class `name` attribute must match the manifest `name` field.
- Validate your params yourself, or expose a JSON Schema (get_schema) so the backend UI validates for you.
- If you declare get_output_schema(), the host validates your result data against it; violations return HTTP 500.


### Schema-driven UI hints (2 minutes)
Shu UIs render forms directly from your JSON Schema and support a few helper extensions:
- x-ui.hidden (or x_ui.hidden): hide a field from the UI. Use when the value is bound by the host.
- x-ui.help (or x_ui.help): short help text shown via a question-mark tooltip next to the field.
- x-binding: auto-populate from context, e.g. `identity:google_email`, `secret:my_key`, or `env:MY_VAR`.
- x-ui.enum_labels: map enum values to human-friendly labels.
- x-ui.placeholder: placeholder text for the implicit Auto option when the field allows null.
- x-ui.show_when: conditionally render a field based on other values.

Example (no auth fields in params; host injects tokens via capabilities):
```
{
  "since_hours": {
    "type": "integer",
    "default": 48,
    "x-ui": {"help": "Look-back window in hours"}
  },
  "query": {
    "type": ["string", "null"],
    "x-ui": {"help": "Optional search filter"}
  }
}
```

#### Per‑option help for enums (x-ui.enum_help)
- Use `x-ui.enum_help` to attach help text to each enum option in a select field. The UI shows these as option tooltips, and also displays the selected option’s help as helper text under the control.

Example:
```
{
  "op": {
    "type": "string",
    "enum": ["ingest", "digest"],
    "x-ui": {
      "enum_labels": {"ingest": "Ingest full content", "digest": "Create summary"},
      "enum_help": {"ingest": "Pull full items into the Knowledge Base.", "digest": "Summarize into a single Knowledge Object."}
    }
  }
}
```


### Capability-driven Auth (AUTH-REF-001) — op_auth.allowed_modes with Google (no fallback)
Do NOT put auth fields (auth_mode, user_email, impersonate_email) in your params. Instead:
- In manifest: declare `required_identities` and per-operation `op_auth` with:
  - `provider`: e.g., "google"
  - `mode`: the default mode for this op (e.g., "user")
  - `allowed_modes`: list of modes this op supports (UI will only offer these)
  - `scopes`: OAuth scopes required for the provider
  - `subject_hint`: optional hint for where to pull an impersonation subject from (e.g., `identity:google_email`)

Example manifest snippet:
```python
"required_identities": [
  {
    "provider": "google",
    "mode": "user",
    "scopes": ["https://www.googleapis.com/auth/gmail.readonly"]
  }
],
"op_auth": {
  "digest": {
    "provider": "google",
    "mode": "user",
    "allowed_modes": ["user", "domain_delegate"],
    "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
    "subject_hint": "identity:google_email"
  }
}
```

Runtime selection and strict enforcement:
- The UI intersects host capabilities with `allowed_modes` and passes the user’s choice to the backend under `params.__host.auth.google = { mode, subject? }`.
- Plugins must honor the selection exactly. Do not implement silent fallbacks.

Example plugin code (single call):
```python
# Let the host resolve selection and scopes from the manifest/UI
access_token, target = await host.auth.resolve_token_and_target("google")
if not access_token:
  raise RuntimeError("No Google access token. Connect an account or configure delegation.")
# target is account email (user mode), impersonation email (domain_delegate), or None (service_account)
```

Notes:
- Use `allowed_modes` to restrict choices to what your op actually supports. Available modes: `user`, `domain_delegate`, `service_account`.
- Prefer `domain_delegate` (service account impersonation) over direct `service_account` unless your op truly works without a user context.
- If requirements for the selected/default mode are not met, raise a clear error; do not silently switch modes.
- The host mediates OAuth flows, enforces scopes, and prevents cross-user access according to RBAC.

### Declaring Secret Requirements (op_auth.secrets)
Plugins that need API keys or other non-OAuth credentials can declare secret requirements per-op:

```python
"op_auth": {
  "search": {
    "provider": "google",
    "mode": "user",
    "scopes": ["..."],
    # Secret requirements for this op
    "secrets": {
      "api_key": {"allowed_scope": "system_or_user"},  # User override or admin default
      "user_token": {"allowed_scope": "user"}           # Must be configured by user
    }
  }
}
```

Secret scope values:
- `"user"`: Secret must be configured by the user (per-user override only)
- `"system"`: Secret must be configured by admin (system-wide shared secret only)
- `"system_or_user"` (default): User secret preferred, system secret as fallback

At execution time, the host enforces that all declared secrets are available per their `allowed_scope`. If a required secret is missing, execution fails with a `missing_secrets` error.

Users configure their secrets via the Plugin Subscriptions page (/settings/connected-accounts). Admins configure system secrets via Plugins Admin.

### Using HostCapabilities (10 minutes)
1) HTTP (host.http)
- Use host.http.fetch(method, url, headers=..., params=..., json=...)
- Policy: certain direct client imports are blocked; always use host.http

2) Identity (host.identity)
- host.identity.user_email is available if your app user has an email

3) Auth (host.auth)
- Provider-agnostic methods (AUTH-REF-001):
  - resolve_token_and_target(provider: str) -> Tuple[Optional[str], Optional[str]]  // scopes and mode come from manifest/UI
    - Returns (access_token, target). For Google user mode, target = account email (preferred); falls back to "me" only if unavailable. For domain_delegate, target = subject email. For service_account, target = None.
  - provider_user_token(provider: str, required_scopes?: List[str]) -> Optional[str]
  - provider_service_account_token(provider: str, scopes: List[str], subject?: str) -> str
- Legacy helpers (still available if you need direct control):
  - refresh_access_token(token_url, client_id, refresh_token, client_secret)

4) Secrets (host.secrets)
- get(key): Reads with user->system fallback (user secret preferred, system secret as default)
- set(key, value): Writes to user scope only
- delete(key): Deletes from user scope only
- Values are encrypted at rest; declare requirements in op_auth.secrets for enforcement

5) Storage (host.storage)
- put(key, value), get(key), delete(key)
- For non‑sensitive, small JSON data (e.g., cursors, last run timestamps)

6) Knowledge Base (host.kb)
- **Type-specific ingestion methods** (recommended):
  - `ingest_document(kb_id, *, file_bytes, filename, mime_type, source_id, source_url?, attributes?)` → returns `{ ko_id, document_id, word_count, character_count, chunk_count, extraction }`
  - `ingest_email(kb_id, *, subject, sender?, recipients: {to, cc, bcc}, date?, message_id, thread_id?, body_text?, body_html?, labels?, source_url?, attributes?)` → returns `{ ko_id, ... }`
  - `ingest_thread(kb_id, *, title, content, thread_id, source_url?, attributes?)` → returns `{ ko_id, ... }`
  - `ingest_text(kb_id, *, title, content, source_id, source_url?, attributes?)` → returns `{ ko_id, ... }`
- **Generic method** (use when type-specific methods don't fit):
  - `upsert_knowledge_object(kb_id, ko_dict_or_model)` → returns `ko_id`
- **Why use type-specific methods?**
  - Centralize extraction (OCR/text), metadata persistence, dedupe, chunking, and embeddings
  - Plugins avoid free-handing KO construction
  - Host applies per-feed OCR policy automatically (always|auto|fallback|never)
  - Extraction metadata (method, engine, confidence, duration) is persisted automatically

Example using ingest_document:
<augment_code_snippet mode="EXCERPT">
````python
result = await host.kb.ingest_document(
  kb_id,
  file_bytes=blob,
  filename=name,
  mime_type=mt,
  source_id=file_id,
  source_url=webViewLink,
  attributes={"modified_at": modified_at}
)
# result contains: ko_id, document_id, word_count, character_count, chunk_count, extraction
````
</augment_code_snippet>

Example using upsert_knowledge_object (legacy/custom):
<augment_code_snippet mode="EXCERPT">
````python
ko = {
  "type": "email_digest",
  "source": {"plugin": "gmail_digest", "account": user_email},
  "external_id": f"{user_email}:{int(window_start)}:{int(window_end)}",
  "title": title,
  "content": content,
  "attributes": {"message_count": len(msgs)}
}
ko_id = await host.kb.upsert_knowledge_object(kb_id, ko)
````
</augment_code_snippet>

### Preview/Approval pattern for actions (3 minutes)
- If your plugin can perform side‑effects, implement a preview step and an approval gate.
- Pattern:
  - If params.preview is true and not approve: return a plan (no changes)
  - If approve is not true: return an error with code "approval_required" and the plan
  - If approve is true: perform the action and return result + plan
- UI will show "Preview" and "Approve & Run" automatically if your schema declares preview/approve
- **Important**: Side-effecting ops should NOT be in `allowed_feed_ops` or `chat_callable_ops`

### Plugin validation and import guards (2 minutes)
The loader enforces security policies at sync time:
- **Disallowed imports**: Plugins cannot import direct HTTP clients (`requests`, `httpx`, `urllib3`, `urllib.request`) or host-internal modules (`shu.*`)
- **Static scan**: The loader scans all `.py` files in your plugin directory for disallowed imports
- **Load-time enforcement**: If violations are found, the plugin fails to load with a clear error message
- **Module reload**: When you sync plugins, the loader uses `importlib.reload` to pick up changes without restarting the server
- **Op enum requirement**: Your input schema MUST include an `op` field with at least one enum value; the loader validates this at load time

### Register and run your plugin (3 minutes)
- Sync plugins to the Plugin Registry:
  - POST /api/v1/plugins/admin/sync (Admin)
  - This discovers plugins, validates manifests, scans for violations, and registers them in the database
- Enable the plugin (if disabled):
  - PATCH /api/v1/plugins/admin/{name}/enable {"enabled": true}
- Execute from UI: Plugins Admin → your plugin → Execute
- Execute via API:
  - POST /api/v1/plugins/{name}/execute {"params": {...}}


### Diagnostics in outputs (non-error details)
- Prefer returning developer-facing, non-error details under `diagnostics` in your result data rather than `warnings`.
- Gate verbose diagnostics behind either a per-call parameter `debug=true` (declare it in your input schema) or a host environment flag (e.g., `settings.DEBUG`).
- Example schema field:
  - `"debug": {"type": ["boolean","null"], "default": null, "x-ui": {"help": "Include diagnostic info in output diagnostics", "hidden": true} }`

### User-facing skip reasons (feeds and ingestion)
- When a background feed skips items (size limit, extension filter, unsupported format, empty extraction), include a concise, user-facing reason in the result `diagnostics` array even when `debug` is false.
  - Example messages:
    - `skip:too_large id=... name=... size=... max=...`
    - `skip:ext_filtered id=... name=... ext=... allowed=...`
    - `skip:unsupported_format id=... name=... mime=... ct=... ext=...`
    - `skip:empty_extraction id=... name=...`
- Keep developer-only details under `diagnostics` gated by `debug=true` (e.g., `diag:*` entries like cursors and page tokens), but do not hide user-facing skip reasons.

### Enabling debug output
- One-off execution (chat or admin): include `{"debug": true}` in `params`.
- Feeds (schedules): set `params.debug = true` when creating or patching the feed:
  - Create: `POST /api/v1/plugins/admin/feeds` with body `{ name, plugin_name, params: { ..., debug: true }, ... }`
  - Update: `PATCH /api/v1/plugins/admin/feeds/{id}` with body `{ params: { ..., debug: true } }`
  - Run-now uses the feed’s saved `params`.

### Background schedules (Feeds) (5 minutes)
- Use the built‑in interval scheduler to run plugins periodically (e.g., hourly).
- Feed-safe ops: plugins can declare which operations are safe for Feeds using manifest keys `allowed_feed_ops` and `default_feed_op`.
  - If a whitelist is present, the host enforces it on create/update; if `op` is omitted and a default exists, it will be injected.
- Create schedule (Admin):
  - POST /api/v1/plugins/admin/feeds
  - Body:
    {
      "name": "Gmail hourly digest",
      "plugin_name": "gmail_digest",
      "params": {"kb_id": "<KB_ID>", "since_hours": 6},  // op omitted; host injects default_feed_op=digest
      "interval_seconds": 3600,
      "enabled": true
    }
- Enqueue due runs (e.g., via cron or a K8s job):
  - POST /api/v1/plugins/admin/feeds/run-due

### Security and guardrails
- Capability enforcement: host only exposes what you declare. Access to undeclared host.* raises an error.
- Import guard: HTTP client imports are denied during execution; use host.http
- Secrets: never hardcode credentials; use host.secrets
- OAuth: use host.auth helpers and host.auth.create/verify_oauth_state for CSRF protection in web flows

### Troubleshooting
- "capability not available": add the capability to your manifest
- "approval_required": your action path requires approve=true; first call with preview=true to see the plan
- "No Google access token": connect an account (host.auth.provider_user_token) or configure service account delegation (host.auth.provider_service_account_token)
- KB write errors: ensure you pass a valid kb_id and your user has access

### What a complete plugin looks like
See these real examples in the codebase:

**plugins/shu_gmail_digest** - Full-featured email plugin:
- Declares capabilities: http, identity, auth, secrets, storage, kb (cursor auto-included)
- Supports multiple ops: list, mark_read, archive, digest, ingest
- Uses `allowed_feed_ops: ["ingest"]` and `chat_callable_ops: ["list", "digest"]`
- Implements `op_auth` with `allowed_modes: ["user", "domain_delegate"]`
- Uses `host.kb.ingest_email()` for type-specific ingestion

**plugins/shu_gdrive_files** - Document ingestion plugin:
- Declares capabilities: http, identity, auth, storage, kb, ocr
- Uses `host.kb.ingest_document()` with automatic OCR policy enforcement
- Supports `allowed_modes: ["user", "domain_delegate", "service_account"]`

**plugins/shu_gchat_digest** - Chat messages plugin:
- Uses `host.cache` to cache Admin Directory user lookups (TTL ~6h)
- Demonstrates read-only list op (chat-callable) and ingest op (feed-safe)
