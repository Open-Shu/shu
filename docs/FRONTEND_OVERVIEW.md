# Frontend Overview (for Augment Agent)

This document is the quick reference for the React frontend located at `./frontend`. It exists in the same repository as the backend and must be treated as first‑class context during every session startup.

## What exists right now
- Framework: React 18
- Libraries: Material‑UI (MUI), React Query, Axios, React Router
- Admin Console: Dashboard, Knowledge Base Management, Plugin Feeds, Query Tester, Health Monitor
- Dev server: `npm start` (http://localhost:3000)
- Build: `npm run build`
- API base URL: `REACT_APP_API_BASE_URL` (optional). If unset, frontend assumes same‑origin behind ingress
- Source layout (from README):
  - `src/components/` — Dashboard, KnowledgeBases, PluginsAdmin, PluginsAdminFeeds, QueryTester, LLMTester, HealthMonitor, ModernChat, etc.
  - `src/layouts/` — AdminLayout, UserLayout
  - `src/services/api.js` — Axios configuration

## Session Startup Checklist (Frontend)

## Frontend-first principle
- Treat UI/UX as a primary surface: build API features with clear, intuitive UI flows in mind (Plugins Admin Feeds, KB Feeds, Chat Plugin Picker)
- Replace legacy “Sync Jobs” terminology with Plugin Feeds and in-chat tools as soon as parity is reached
- Avoid parallel systems; reuse contracts/components (schema→form, identity gating, approvals)

Always do this at session start (parallel to backend docs):
1) Open `./frontend/README.md` and capture:
   - Dev commands, build commands
   - Environment variable usage (e.g., `REACT_APP_API_BASE_URL`)
   - High‑level features/components
2) Open `./frontend/package.json` and detect scripts:
   - `start`, `build`, `test` (and any others)
3) Detect package manager:
   - Prefer npm by default; if `yarn.lock` or `pnpm-lock.yaml` exists, record that and their commands
4) Record dev/build/test commands into session memory so you can reference/run them without asking the user
5) When backend changes surface in UI (e.g., new endpoints or schemas), update this file and the frontend README if behavior changes

## API Integration Reference
- HTTP Client: Axios (see `src/services/api.js`)
- Data fetching: React Query
- Primary backend endpoints currently referenced in README:
  - Health checks: `/health/*`
  - Knowledge Bases: `/knowledge-bases/*`
  - Chat: `/chat/*`
  - Plugins: `/plugins/*`
  - Plugin Feeds (Admin): `/plugins/admin/feeds/*`
  - Query: `/query/*`

### Plugins v1 endpoints to use from UI (API path: /plugins)
- `GET /api/v1/plugins` — list plugins
- `GET /api/v1/plugins/{name}` — plugin details
- `POST /api/v1/plugins/{name}/execute` — execute plugin
- Admin:
  - `PATCH /api/v1/plugins/admin/{name}/enable`
  - `POST /api/v1/plugins/admin/sync`

  - Feeds (Admin):
    - GET /api/v1/plugins/admin/feeds
    - POST /api/v1/plugins/admin/feeds
    - POST /api/v1/plugins/admin/feeds/{id}/run-now
    - PATCH /api/v1/plugins/admin/feeds/{id}


Note: When a plugin violates its declared output schema, the API returns HTTP 500 with error=output_validation_error. The UI should display this clearly to admins.

Actual admin routes and placement (current):
- `/admin/plugins` → `src/components/PluginsAdmin.js` — list/sync/enable/execute
- `/admin/feeds` → `src/components/PluginsAdminFeeds.js` — plugin feeds
- `/settings/connected-accounts` → `src/components/ConnectedAccountsPage.jsx` — Plugin Subscriptions (User layout)


Update (2025-10-09): Plugin Subscriptions (formerly Connected Accounts) now uses server-computed scope unions.
- IdentityStatus prop: `useServerUnionForAuthorize` enables omitting scopes when calling /host/auth/authorize; backend computes from subscriptions.
- API methods in src/services/api.js: hostAuthAPI.consentScopes, listSubscriptions, subscribe, unsubscribe.
- ConnectedAccountsPage renders per-provider subscription checkboxes with tooltips listing plugin-declared scopes.

Update (2025-12-09): Plugin Subscriptions page now includes a Plugin Secrets section.
- Displays plugins that declare op_auth[op].secrets requirements
- Shows required keys and which are already configured
- Allows users to set, update, or delete their per-plugin secrets via /plugins/self/{name}/secrets endpoints
- API methods in src/services/pluginsApi.js: listSelfSecrets, getSelfSecret, setSelfSecret, deleteSelfSecret
- Admin can manage system-scoped secrets via PluginSecretsEditor with scope toggle (system vs user)


## UI Standards: Help Tooltip Icons
- Standard component: use `HelpTooltip` for all inline help question-mark icons
  - Path: `frontend/src/components/HelpTooltip.jsx`
  - Behavior: small HelpOutline icon inside a small IconButton wrapped in a Tooltip; default placement "right"
  - Usage example:

  ```jsx
  <HelpTooltip title="Explain what this control does" />
  ```

- Migration notes
  - Prefer `HelpTooltip` over ad-hoc `<Tooltip><IconButton><HelpOutlineIcon/></IconButton></Tooltip>` constructions
  - If an exception is needed (e.g., different icon or placement), document it in the task file and keep usage consistent
- Current adoption
  - ConnectedAccountsPage uses HelpTooltip
  - KBConfigDialog is being migrated (remaining instances will be replaced incrementally)

- KB feeds management is the Feeds tab under KB Documents: `src/components/Documents.js` (tab) + `src/components/KBPluginFeedsTab.jsx` — deep link: `/admin/knowledge-bases/:kbId/documents?tab=feeds`
- React Router + React Query + Axios via `src/services/api.js`


## LLM Providers Admin UI
- Provider Types are loaded read-only via GET /llm/provider-types and /llm/provider-types/{key}. They provide base_url_template, endpoints, streaming hints, and parameter_mapping used to scaffold forms; they are not editable in the UI.
- Providers (admin-only) can be created/edited. Admins set api_endpoint and may provide endpoints_override that mirrors the Provider Type endpoints shape but only includes fields that differ. The UI initializes api_endpoint from the provider type base_url_template.
- Model Configurations (admin-only) control LLM behavior via parameter_overrides. Typed controls are rendered from ProviderTypeDefinition.parameter_mapping; an Advanced JSON editor allows unknown keys to pass through. Null/empty values are pruned before submission. No per-chat params are accepted; chat uses only stored overrides.
- See docs/contracts/LLM_PROVIDER_CONTRACT.md for validation, mapping, endpoint options (message_input/message_output), and streaming policy.

### LLM Providers — Known Issues
- Support for Bedrock still needs to be added, Azure still needs to be tested.
- Streaming paths are still hardcoded, we can generalize those in the future.
- Currently no authorization header override (locked into provider)

## Environment & Configuration
- `REACT_APP_API_BASE_URL` (optional): If omitted, app expects same‑origin API
- When running locally:
  - Backend: http://localhost:8000
  - Frontend: http://localhost:3000
  - Set `REACT_APP_API_BASE_URL=http://localhost:8000` to avoid CORS if not using same‑origin proxy


- Chat UI (sliding window) environment variables (build-time; consumed via chatConfig.js):
  - REACT_APP_CHAT_WINDOW_SIZE
  - REACT_APP_CHAT_OVERSCAN
  - REACT_APP_CHAT_SCROLL_TOP_THRESHOLD_PX
  - REACT_APP_CHAT_SCROLL_BOTTOM_THRESHOLD_PX
  - REACT_APP_CHAT_PAGE_SIZE
  - REACT_APP_SUMMARY_SEARCH_DEBOUNCE_MS
  - REACT_APP_SUMMARY_SEARCH_MIN_TERM_LENGTH
  - REACT_APP_SUMMARY_SEARCH_MAX_TOKENS
- Defaults live in `frontend/src/components/chat/ModernChat/utils/chatConfig.js`. Components import from chatConfig; do not read `process.env` directly.

## Development Commands (expected)

Streaming completion contract:
- Backend emits a final SSE event before [DONE]: {"event":"final_message","content": <Message>}
- Frontend uses this to replace the streaming placeholder without any follow-up fetch

- Install: `cd frontend && npm install`
- Dev: `npm start`
- Build: `npm run build`
- Test: `npm test` (if present; verify in package.json)

## Known Issues / Limitations
- Some backend endpoints may not yet have UI surfaces beyond admin (end‑user chat plugin usage is a separate slice). Admin UIs for Plugins and Feeds are present.
- README lists features but not all are guaranteed wired to current API behavior; confirm before modifying
- Exact scripts (`test`, `lint`, etc.) depend on `package.json` content; always verify on startup

## Security Considerations
- Browser‑side config is public; do not put secrets in `REACT_APP_*`
- Ensure auth tokens are stored per current auth flow in `useAuth.js` and sent via Axios interceptors (verify before changes)
- CORS: Keep same‑origin where possible; otherwise configure API CORS as needed for dev

## How Augment Agent should use this
- Treat frontend as “always present”; never ask where it is
- Load this document and `frontend/README.md` on every session start
- When implementing backend features that require UI:
  - Add/update the UI plan in this doc (section above)
  - Update task files to reflect UI work (if not already present)
  - Defer actual UI edits if the user requested backend‑only scope; otherwise implement using the scripts and patterns above
