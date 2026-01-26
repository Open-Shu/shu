# Microsoft 365 OAuth Setup Guide (Shu Host Auth)

Implementation Status: Partial

- User OAuth: Implemented via MicrosoftAuthAdapter (authorization code + refresh token)
- Service account / Application permissions (client credentials): Not implemented
- Delegation (domain-wide) equivalent: Not implemented (see "Is there a DWD equivalent?" below)

Limitations / Known Issues

- PKCE is not used currently; the adapter expects client secret on the backend
- Identity upsert (ProviderIdentity) during exchange is only implemented for Google today; Microsoft identity is not yet persisted on connect
- Teams/Chat compliance APIs and some application permissions require special Microsoft approval and are out of scope for this slice

Security Notes

- Keep client secret out of frontend; set only via environment variables
- Request the minimum scopes necessary; tokens are stored encrypted via ProviderCredential

---

## 1) Prerequisites

- Azure AD/Entra admin or contributor with permission to register apps
- Shu environment running with API reachable by your browser
- The callback URI: Shu uses a **shared callback endpoint** for all OAuth providers at `/auth/callback` (public alias) or `/api/v1/host/auth/callback`. The provider is identified via the `state` parameter.

Environment variables (set in `.env`):

- `MICROSOFT_CLIENT_ID`
- `MICROSOFT_CLIENT_SECRET`
- `OAUTH_REDIRECT_URI` - Shared callback URL for all providers (e.g., `http://localhost:8000/auth/callback`)
- `MICROSOFT_TENANT_ID` (optional; defaults to "common" for multi-tenant)

These map to `src/shu/core/config.py` settings: `microsoft_client_id`, `microsoft_client_secret`, `oauth_redirect_uri`, `microsoft_tenant_id`

Note: Shu uses a single provider-agnostic callback endpoint. The `OAUTH_REDIRECT_URI` setting is shared by all OAuth providers (Google, Microsoft, etc.). Legacy `GOOGLE_REDIRECT_URI` and `MICROSOFT_REDIRECT_URI` are still supported for backward compatibility but deprecated.

---

## 2) Tenant Alignment (Azure Portal + Microsoft 365)

The app registration must be in the **same Azure AD tenant** as your Microsoft 365 users, or configured as multi-tenant. This is a common issue when Azure Portal and M365 Admin Center are accessed with different accounts.

**Check which tenant you're in:**

- Azure Portal: Click your profile icon (top right) → "Switch directory" to see available tenants
- M365 Admin Center: Settings → Org settings → Organization profile → Tenant ID

### Option A: Register app in your M365 tenant (Recommended for single-tenant)

Your M365 subscription includes an Azure AD tenant. Use it:

1. Sign into [Azure Portal](https://portal.azure.com) using your **M365 admin email**
2. If prompted, create an Azure account with that email (no subscription needed for app registrations)
3. Register the app there - it will be in the same tenant as your M365 users

### Option B: Make your app multi-tenant

If the app is in a different Azure tenant:

1. Go to Azure Portal → App registrations → Your app → Authentication
2. Under "Supported account types", select:
   - "Accounts in any organizational directory (Any Azure AD directory - Multitenant)"
3. Save
4. Set `MICROSOFT_TENANT_ID` to `common` (or leave unset)

### Option C: Add your Azure account to M365 tenant

1. In M365 Admin Center, add your Azure portal email as a user with Global Administrator role
2. Now both portals are accessible from the same account

---

## 3) Register an App in Azure Portal (Entra ID)

1. Go to Azure Portal → Microsoft Entra ID → App registrations → New registration
2. Name: Shu (local) or similar
3. Supported account types:
   - Single tenant: Users in your M365 tenant only (set `MICROSOFT_TENANT_ID` to your tenant ID)
   - Multi-tenant: Users from any Azure AD tenant (set `MICROSOFT_TENANT_ID` to `common`)
4. Redirect URI: Web → set to your `OAUTH_REDIRECT_URI` (e.g., `http://localhost:8000/api/v1/host/auth/callback`)
5. Register
6. In the app:
   - Certificates & secrets → Client secrets → New client secret → copy the value (set `MICROSOFT_CLIENT_SECRET`)
   - Overview → Application (client) ID → set `MICROSOFT_CLIENT_ID`
   - If single tenant, Directory (tenant) ID → set `MICROSOFT_TENANT_ID`

---

## 4) Configure API Permissions (Scopes)

- Go to API permissions → Add a permission → Microsoft Graph → Delegated permissions
- Add only what you need, e.g.:
  - Mail.Read, Mail.ReadBasic (Gmail analog)
  - Files.Read, Files.Read.All, Sites.Read.All (OneDrive/SharePoint)
  - Calendars.Read (Calendar)
  - offline_access (required for refresh tokens; also added automatically by Shu)
- Click "Grant admin consent" for your tenant if your organization requires admin consent

Notes

- The adapter will include offline_access automatically during both authorize and exchange
- Scopes requested by Shu must also be enabled/consented in the app registration; insufficient consent will cause exchange to fail or later API calls to be unauthorized

---

## 5) Wire Shu Environment

Set the env vars and restart Shu so settings load:

- `MICROSOFT_CLIENT_ID`
- `MICROSOFT_CLIENT_SECRET`
- `OAUTH_REDIRECT_URI` (must match exactly the URI configured in Azure)
- `MICROSOFT_TENANT_ID` (or leave unset to use common)

---

## 6) Connect a Microsoft Account (User OAuth)

You can use the Connected Accounts UI (preferred), or call the Host Auth endpoints directly.

API flow

1. Build authorization URL
   - GET `/api/v1/host/auth/authorize?provider=microsoft&scopes=scope1,scope2`
   - Example scopes: Files.Read,Mail.Read,offline_access (offline_access will be added automatically if omitted)
2. Browser completes consent and is redirected to `OAUTH_REDIRECT_URI` (`/api/v1/host/auth/callback`)
3. Exchange the code
   - POST `/api/v1/host/auth/exchange` with JSON:
     `{"provider":"microsoft","code":"<code>","scopes":["Files.Read","Mail.Read"]}`

Quick curl examples

```bash
# 1) Get authorization URL (open in browser)
curl -s "http://localhost:8000/api/v1/host/auth/authorize?provider=microsoft&scopes=Files.Read,Mail.Read" -H "Authorization: Bearer <your_api_token>"

# After completing consent in browser, capture `code` from the redirect handler (the UI handles this for you).

# 2) Exchange code for tokens
curl -s -X POST http://localhost:8000/api/v1/host/auth/exchange \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your_api_token>" \
  -d '{"provider":"microsoft","code":"<paste-code>","scopes":["Files.Read","Mail.Read"]}'
```

Verification

- GET `/api/v1/host/auth/status?providers=microsoft` returns
  `{ "microsoft": { "user_connected": true|false, "granted_scopes": ["..."] } }`

---

## 7) Using the Token in Plugins

- Plugins should declare per-operation auth in manifest (op_auth) and use host.auth at runtime
- host.auth will obtain a Microsoft access token (refresh if needed) when provider=microsoft and auth_mode=user
- If required scopes are not a subset of the connected grant, token resolution returns None and the call should surface an auth error

---

## 8) Is there a Google DWD equivalent for Microsoft?

Conceptual equivalent: Application permissions (app-only) using the OAuth 2.0 client credentials grant

- Instead of a user delegating consent, the app itself is granted tenant-wide permissions by an admin
- Examples: Mail.Read (application), Files.Read.All (application), Sites.Read.All (application)
- Exchange Online may additionally require Application Access Policies to scope app access to specific mailboxes
- Some Teams/Chat and compliance endpoints require special Microsoft approval

Current status in Shu

- Not implemented in MicrosoftAuthAdapter
- `service_account_token()` and `delegation_check()` raise/return not implemented
- To support app-only in the future we would:
  - Add client credentials flow in the adapter using `MICROSOFT_CLIENT_ID`/`SECRET` and tenant
  - Allow plugins to request auth_mode=service_account or domain_delegate analog where appropriate
  - Provide readiness checks similar to Google (but Microsoft has no direct delegation check endpoint; readiness is validated by obtaining an app-only token and probing a minimal Graph call)

Recommendation for now

- Use User OAuth for Microsoft integrations in this delivery slice
- For org-wide ingestion requirements, plan a separate task to add application permissions support; ensure admin consent and mailbox scoping policies are addressed

---

## 9) Troubleshooting

- `invalid_client` or `unauthorized_client` during exchange: verify `MICROSOFT_CLIENT_ID`/`SECRET`/`OAUTH_REDIRECT_URI` match the app registration and tenant
- AADSTS50020 (user account does not exist in tenant): app is single-tenant but user is from a different tenant; either register the app in the user's M365 tenant or make the app multi-tenant (see section 2)
- AADSTS65001 (consent required): grant admin consent for requested scopes in Azure Portal or have the user consent if allowed
- AADSTS700016 (application not found): the app registration is in a different tenant than expected; check `MICROSOFT_TENANT_ID` matches where the app is registered
- Token returned but API 403: the scope is missing or the API requires application permission instead of delegated; adjust scopes and/or app permission type
- Callback mismatch: the redirect URI must exactly match the configured value (including path)

---

## 10) References

- [Microsoft identity platform OAuth 2.0 authorization code flow (v2.0)](https://learn.microsoft.com/en-us/azure/active-directory/develop/v2-oauth2-auth-code-flow)
- [Microsoft Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference)
- [Application access policies for Exchange Online](https://learn.microsoft.com/en-us/graph/auth-limit-mailbox-access)
