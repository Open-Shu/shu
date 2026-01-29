**Instructional Document (Internal): Setting Up Domain-Wide Delegation for Gmail and Google Chat with Google Workspace**

**Purpose**
This document provides step-by-step instructions for configuring domain-wide delegation (DWD) in Google Workspace so "Shu" can access Gmail and Google Chat data for users in your organization. Domain-wide delegation enables a service account to impersonate users within the domain without individual consent, which Shu uses for ingestion and diagnostics.

**Prerequisites**
- Admin access to the Google Workspace Admin Console for your organization's domain
- Admin access to Google Cloud Console (GCP) to create and manage service accounts
- A GCP project to house the service account and API configuration
- Basic familiarity with OAuth scopes and secure key management

**Scope**
- **Target Users:** All employees (or a scoped subset) under your organization's Google Workspace domain
- **Services Covered:** Gmail (email), Google Chat (messages/spaces), and Admin SDK (directory)
- **Access Level:** Read-only for ingestion and diagnostics; write scopes are possible but not covered here

**Important Notes**
- DWD grants broad access to data for the authorized scopes. Minimize scopes and protect service account credentials.
- Ensure compliance with your internal policies and relevant regulations (GDPR, etc.).

---

### Part 1: Create a Service Account and Enable APIs (GCP)

1. **Create / choose a project**
   - Go to https://console.cloud.google.com
   - Use an organization project for Shu (e.g., "Shu-Agent-Project").

2. **Enable required APIs**
   - APIs & Services > Library
   - Enable at least:
     - Gmail API
     - Google Drive API (for files ingest)
     - Google Chat API
     - Admin SDK API (for listing domain users)

3. **Create a service account**
   - IAM & Admin > Service Accounts > Create Service Account
   - Example:
     - Name: `shu-agent`
     - Description: `Service account for Shu to access Gmail, Drive, and Chat via DWD`
   - You do **not** need broad project roles for DWD itself; roles are mostly used for GCP control-plane operations.

4. **Generate a JSON key (if using direct JSON)**
   - Service account > Keys > Add Key > Create new key > JSON
   - Download and store securely (vault, Key Vault, Secret Manager, etc.)
   - Never commit the JSON to source control.

5. **Capture the service account Client ID**
   - In the service account details, note the numeric **Unique ID / Client ID** (e.g., `123456789012345678901`).
   - You will use this in the Workspace Admin Console for DWD.

---

### Part 2: Configure Domain-Wide Delegation (Workspace Admin Console)

1. **Open Domain-wide delegation settings**
   - Go to https://admin.google.com with domain admin credentials.
   - Security > Access and Data Control > API controls > Domain-wide delegation > Manage domain-wide delegation.

2. **Add your service account as a delegated client**
   - Click **Add new**.
   - Client ID: paste the service account Client ID from Part 1.
   - OAuth scopes (comma-separated), for example:
     - `https://www.googleapis.com/auth/drive`
     - `https://www.googleapis.com/auth/gmail.readonly`
     - `https://www.googleapis.com/auth/chat.messages.readonly`
     - `https://www.googleapis.com/auth/chat.spaces.readonly`
     - `https://www.googleapis.com/auth/admin.directory.user.readonly`
   - Save and confirm the entry appears in the list.

Notes on Drive scopes:
- Shu currently uses `drive` (full Drive) because some operations (export and Changes API) require broader access than `drive.readonly`.
- You can experiment with narrower scopes, but mismatches will surface as API errors.

---

### Part 3: Readiness Check from Shu

Shu provides a helper to check whether DWD is configured correctly for a given service account client_id and scopes.

Example (Python REPL inside the backend, using host capabilities):

<augment_code_snippet mode="EXCERPT">
````python
from shu.plugins.host.host_builder import make_host
host = make_host(
    plugin_name="diagnostics",
    user_id="u1",
    user_email="you@yourdomain.com",
    capabilities=["http", "auth"],  # type: ignore
)
res = await host.auth.google_domain_delegation_check(
    scopes=["https://www.googleapis.com/auth/drive"],
    subject="admin@yourdomain.com",
)
print(res)  # {ready: True/False, status: 200/401, client_id: "...", error: {...}}
````
</augment_code_snippet>

If you see `status=401` with `unauthorized_client`, verify:
- The Client ID in Admin Console matches the service account Client ID.
- The scopes in Shu's check are included in the Admin Console entry.

---

### Part 4: How Shu Uses DWD Internally

- Service account JSON is referenced by `GOOGLE_SERVICE_ACCOUNT_JSON` in `.env` and `config.py`.
- Plugins and host capabilities request scopes at runtime and impersonate a specific user email.
- DWD is used for:
  - Bulk ingest (Gmail, Drive, and Chat plugins)
  - Readiness checks and diagnostics

High-level flow:
1. Shu loads the service account JSON and requested scopes.
2. Shu signs a JWT assertion and exchanges it for an access token at Google's token endpoint.
3. Shu calls Google APIs on behalf of `subject=user_email` (impersonated user).

---

### Part 5: Example - Listing Users and Accessing Mail (Reference Only)

This is an illustrative snippet (do not commit secrets into code):

<augment_code_snippet mode="EXCERPT">
````python
from google.oauth2 import service_account
from googleapiclient.discovery import build

SERVICE_ACCOUNT_FILE = "path/to/service-account.json"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
]

def get_api_client(service_name: str, version: str, user_email: str):
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
        subject=user_email,
    )
    return build(service_name, version, credentials=creds)

# Example: access Gmail for one user
user_email = "employee@yourdomain.com"
gmail_service = get_api_client("gmail", "v1", user_email)
````
</augment_code_snippet>

For more complex examples, refer to Google's official DWD documentation and adapt scopes and subjects to match Shu's configuration.
