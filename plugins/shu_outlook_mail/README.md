# Outlook Mail Plugin

The Outlook Mail Plugin enables users with Microsoft 365 accounts to integrate their Outlook email with Shu's knowledge base system. This plugin provides three core operations for listing, digesting, and ingesting email messages using the Microsoft Graph API.

## Features

- **List Messages**: Fetch recent emails from your Outlook inbox with flexible filtering
- **Create Digests**: Generate summary reports of inbox activity with sender analysis
- **Ingest Emails**: Add individual emails to your knowledge base for search and retrieval
- **Delta Sync**: Efficient incremental synchronization using Microsoft Graph delta queries
- **Background Feeds**: Automatic email ingestion via scheduled background tasks

## Table of Contents

- [Installation](#installation)
- [OAuth Setup](#oauth-setup)
- [Operations](#operations)
  - [List Operation](#list-operation)
  - [Digest Operation](#digest-operation)
  - [Ingest Operation](#ingest-operation)
- [Delta Sync Behavior](#delta-sync-behavior)
- [Parameters](#parameters)
- [Output Schemas](#output-schemas)
- [Usage Examples](#usage-examples)
- [Error Handling](#error-handling)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)

## Installation

The plugin is located in `shu/plugins/shu_outlook_mail/` and follows the standardized Shu plugin contract.

### Plugin Structure

```text
shu/plugins/shu_outlook_mail/
├── __init__.py          # Package exports
├── manifest.py          # Plugin metadata and configuration
├── plugin.py            # Main plugin implementation
└── README.md            # This documentation
```

### Required Capabilities

The plugin requires the following host capabilities:

- `http`: For Microsoft Graph API calls
- `identity`: For user identity attribution
- `auth`: For OAuth token resolution
- `secrets`: For secure credential storage
- `kb`: For writing Knowledge Objects to the knowledge base
- `cursor`: For delta sync state storage

## OAuth Setup

The Outlook Mail plugin requires Microsoft OAuth authentication with the `Mail.Read` scope.

### Prerequisites

1. **Azure AD/Entra Admin Access**: Permission to register applications
2. **Microsoft 365 Account**: Active M365 subscription with email
3. **Shu Environment**: Running instance with API accessible

### Environment Variables

Set these in your `.env` file:

```bash
# Microsoft OAuth Configuration
MICROSOFT_CLIENT_ID=your_client_id_here
MICROSOFT_CLIENT_SECRET=your_client_secret_here
OAUTH_REDIRECT_URI=http://localhost:8000/auth/callback
MICROSOFT_TENANT_ID=common  # or your specific tenant ID
```

### Azure App Registration

1. **Navigate to Azure Portal**
   - Go to [Azure Portal](https://portal.azure.com)
   - Navigate to Microsoft Entra ID → App registrations → New registration

2. **Configure Application**
   - **Name**: Shu Outlook Mail Plugin (or similar)
   - **Supported account types**: 
     - Single tenant: Users in your M365 tenant only
     - Multi-tenant: Users from any Azure AD tenant (recommended)
   - **Redirect URI**: Web → `http://localhost:8000/auth/callback` (or your production URL)

3. **Get Credentials**
   - **Application (client) ID**: Copy to `MICROSOFT_CLIENT_ID`
   - **Certificates & secrets**: Create new client secret → Copy to `MICROSOFT_CLIENT_SECRET`
   - **Directory (tenant) ID**: Copy to `MICROSOFT_TENANT_ID` (if single tenant)

4. **Configure API Permissions**
   - Go to API permissions → Add a permission → Microsoft Graph → Delegated permissions
   - Add the following scopes:
     - `Mail.Read` (required for all operations)
     - `offline_access` (automatically added by Shu for refresh tokens)
   - Click "Grant admin consent" if required by your organization

### Connecting Your Account

Use the Connected Accounts UI in Shu to connect your Microsoft account:

1. Navigate to Settings → Connected Accounts
2. Find the Microsoft provider section
3. Click "Connect" next to the Outlook Mail plugin
4. Complete the OAuth consent flow in your browser
5. Verify the connection shows as active

**API Method** (alternative):

```bash
# 1. Get authorization URL
curl "http://localhost:8000/api/v1/host/auth/authorize?provider=microsoft&scopes=Mail.Read" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 2. Open URL in browser and complete consent

# 3. Exchange code for tokens
curl -X POST http://localhost:8000/api/v1/host/auth/exchange \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"provider":"microsoft","code":"AUTHORIZATION_CODE","scopes":["Mail.Read"]}'
```

For detailed OAuth setup instructions, see [Microsoft 365 OAuth Setup Guide](../../docs/onboarding/microsoft-365-oauth-setup.md).

## Operations

The plugin provides three operations, each designed for specific use cases.

### List Operation

**Purpose**: Fetch and return recent messages without storing them.

**Use Cases**:
- Quick inbox preview
- Testing email filters
- Chat-based email queries
- Manual email review

**Behavior**:
- Fetches messages from `/me/mailFolders/inbox/messages`
- Applies time-based filtering using `since_hours`
- Supports custom OData filters via `query_filter`
- Returns message metadata (id, subject, from, to, receivedDateTime, bodyPreview)
- Does not modify the knowledge base

**Chat Callable**: ✅ Yes (safe read-only operation)

**Example**:
```json
{
  "op": "list",
  "since_hours": 24,
  "max_results": 10
}
```

### Digest Operation

**Purpose**: Create a summary report of inbox activity.

**Use Cases**:
- Daily/weekly inbox summaries
- Sender analysis and trends
- Email volume monitoring
- Quick inbox overview

**Behavior**:
- Fetches messages using list operation logic
- Analyzes messages to identify top senders (up to 10)
- Extracts recent message subjects (up to 20)
- Creates a Knowledge Object with type "email_digest"
- Writes digest to knowledge base if `kb_id` provided
- Returns digest summary with sender statistics

**Chat Callable**: ✅ Yes (creates digest KO but safe for chat)

**Example**:
```json
{
  "op": "digest",
  "since_hours": 168,
  "kb_id": "kb-123"
}
```

### Ingest Operation

**Purpose**: Add individual emails to the knowledge base for search and retrieval.

**Use Cases**:
- Building searchable email archive
- RAG-based email search
- Background feed synchronization
- Email knowledge base population

**Behavior**:
- Validates `kb_id` parameter (required)
- Uses delta sync when cursor exists (incremental updates)
- Fetches full message content including body
- Extracts all email fields (subject, sender, recipients, date, body)
- Calls `host.kb.ingest_email()` for each message
- Handles message deletions via `host.kb.delete_ko()`
- Stores delta token for next sync
- Tracks ingestion and deletion counts

**Chat Callable**: ❌ No (write operation, feed-only)

**Feed Operation**: ✅ Yes (default feed operation)

**Example**:
```json
{
  "op": "ingest",
  "kb_id": "kb-123",
  "since_hours": 48
}
```

## Delta Sync Behavior

The plugin implements efficient incremental synchronization using Microsoft Graph delta queries.

### How Delta Sync Works

1. **Initial Sync** (no cursor exists):
   - Fetches messages using standard list endpoint with time filter
   - Makes delta query to get initial delta token
   - Stores delta token via `host.cursor.set(kb_id, delta_token)`
   - Ingests all fetched messages

2. **Incremental Sync** (cursor exists):
   - Retrieves cursor via `host.cursor.get(kb_id)`
   - Uses delta endpoint: `/me/mailFolders/inbox/messages/delta`
   - Processes only changed messages since last sync
   - Handles `messageAdded` events (new messages)
   - Handles `messageDeleted` events (removed messages)
   - Updates cursor with new delta token

3. **Cursor Reset** (410 Gone error or `reset_cursor=true`):
   - Falls back to full list-based sync
   - Deletes old cursor
   - Establishes new delta token
   - Ingests all messages in time window

### Delta Event Types

- **messageAdded**: New message appears without `@removed` field → Ingest message
- **messageDeleted**: Message appears with `@removed.reason = "deleted"` → Delete from KB
- **messageUpdated**: Existing message with updated fields → Treated as add (re-ingest)

### Cursor Storage

Cursors are stored per knowledge base:

```python
# Cursor format
{
  "kb_id": "kb-123",
  "delta_token": "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$deltatoken=abc123..."
}
```

### Benefits

- **Efficiency**: Only processes changed messages, not entire inbox
- **Scalability**: Handles large mailboxes without re-processing
- **Reliability**: Automatic fallback to full sync on token expiration
- **Accuracy**: Tracks both additions and deletions

### Limitations

- Delta tokens expire after ~30 days of inactivity (triggers full sync)
- Delta sync only available for ingest operation
- Filters applied during initial sync may not apply to delta updates

## Parameters

All operations support the following parameters:

### `op` (string, optional)

**Description**: Operation to perform

**Values**: `"list"`, `"digest"`, `"ingest"`

**Default**: `"ingest"`

**Example**: `"op": "list"`

### `since_hours` (integer, optional)

**Description**: Look-back window in hours for messages

**Range**: 1 to 3360 (1 hour to 140 days)

**Default**: 48

**Example**: `"since_hours": 168` (7 days)

### `query_filter` (string, optional)

**Description**: OData filter expression for advanced filtering

**Format**: OData v4 filter syntax

**Default**: None

**Examples**:
- `"from/emailAddress/address eq 'user@example.com'"` - Messages from specific sender
- `"hasAttachments eq true"` - Messages with attachments
- `"importance eq 'high'"` - High priority messages
- `"subject eq 'Meeting'"` - Messages with specific subject

**Reference**: [OData Filter Syntax](https://learn.microsoft.com/en-us/graph/query-parameters#filter-parameter)

### `max_results` (integer, optional)

**Description**: Maximum number of messages to return

**Range**: 1 to 500

**Default**: 50

**Example**: `"max_results": 100`

### `kb_id` (string, required for ingest)

**Description**: Target knowledge base ID for storing emails

**Required**: Only for `op=ingest`

**Hidden**: Yes (set by system, not user-facing)

**Example**: `"kb_id": "kb-123"`

### `reset_cursor` (boolean, optional)

**Description**: Reset sync cursor and perform full re-ingestion

**Default**: false

**Use Case**: Force full sync when delta sync is out of sync

**Example**: `"reset_cursor": true`

### `debug` (boolean, optional)

**Description**: Enable debug mode with detailed diagnostics

**Default**: false

**Effect**: Includes diagnostics array in output with operational details

**Example**: `"debug": true`

## Output Schemas

### List Operation Output

```json
{
  "messages": [
    {
      "id": "AAMkAGI2...",
      "subject": "Meeting notes",
      "from": {
        "emailAddress": {
          "name": "John Doe",
          "address": "john.doe@example.com"
        }
      },
      "to": [
        {
          "emailAddress": {
            "name": "Jane Smith",
            "address": "jane.smith@example.com"
          }
        }
      ],
      "receivedDateTime": "2024-01-15T10:30:00Z",
      "bodyPreview": "First 255 characters of body..."
    }
  ],
  "count": 42,
  "note": "Retrieved 42 messages from the last 48 hours",
  "diagnostics": []  // Only if debug=true
}
```

### Digest Operation Output

```json
{
  "ko": {
    "type": "email_digest",
    "title": "Outlook Inbox Digest (Jan 15, 2024)",
    "content": "Summary of 42 messages from 15 senders...",
    "attributes": {
      "total_count": 42,
      "top_senders": [
        {
          "email": "john@example.com",
          "name": "John Doe",
          "count": 8
        }
      ],
      "recent_subjects": [
        "Meeting notes",
        "Project update"
      ],
      "window": {
        "since": "2024-01-13T10:30:00Z",
        "until": "2024-01-15T10:30:00Z",
        "hours": 48
      }
    },
    "source_id": "outlook_mail_digest_kb-123_20240115103000",
    "external_id": "outlook_mail_digest_kb-123_20240115103000"
  },
  "count": 42,
  "window": {
    "since": "2024-01-13T10:30:00Z",
    "until": "2024-01-15T10:30:00Z",
    "hours": 48
  },
  "diagnostics": []  // Only if debug=true
}
```

### Ingest Operation Output

```json
{
  "count": 15,
  "deleted": 2,
  "history_id": "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$deltatoken=xyz789...",
  "skips": [
    {
      "item_id": "AAMkAGI3...",
      "reason": "Failed to ingest message: Missing required field",
      "code": "ingestion_failed"
    }
  ],
  "diagnostics": []  // Only if debug=true
}
```

## Usage Examples

### Example 1: List Recent Messages

```python
# List messages from the last 24 hours
params = {
    "op": "list",
    "since_hours": 24,
    "max_results": 20
}

result = await plugin.execute(params, context, host)

if result.status == "success":
    messages = result.data["messages"]
    print(f"Found {len(messages)} messages")
    for msg in messages:
        print(f"- {msg['subject']} from {msg['from']['emailAddress']['address']}")
```

### Example 2: Filter Messages by Sender

```python
# List messages from a specific sender
params = {
    "op": "list",
    "since_hours": 168,  # Last 7 days
    "query_filter": "from/emailAddress/address eq 'boss@company.com'",
    "max_results": 50
}

result = await plugin.execute(params, context, host)
```

### Example 3: Create Weekly Digest

```python
# Create a digest of the last week's emails
params = {
    "op": "digest",
    "since_hours": 168,  # 7 days
    "kb_id": "kb-weekly-digest",
    "max_results": 200
}

result = await plugin.execute(params, context, host)

if result.status == "success":
    digest = result.data["ko"]
    print(f"Digest: {digest['title']}")
    print(f"Total messages: {digest['attributes']['total_count']}")
    print("Top senders:")
    for sender in digest['attributes']['top_senders']:
        print(f"  - {sender['name']}: {sender['count']} messages")
```

### Example 4: Ingest Emails (Initial Sync)

```python
# First-time ingestion of recent emails
params = {
    "op": "ingest",
    "kb_id": "kb-email-archive",
    "since_hours": 720,  # Last 30 days
    "max_results": 500
}

result = await plugin.execute(params, context, host)

if result.status == "success":
    print(f"Ingested {result.data['count']} messages")
    print(f"Delta token stored: {result.data.get('history_id', 'N/A')}")
```

### Example 5: Incremental Sync (Delta)

```python
# Subsequent ingestion using delta sync
# The plugin automatically uses delta sync if a cursor exists
params = {
    "op": "ingest",
    "kb_id": "kb-email-archive"
    # No since_hours needed - delta sync handles it
}

result = await plugin.execute(params, context, host)

if result.status == "success":
    print(f"Ingested {result.data['count']} new messages")
    print(f"Deleted {result.data['deleted']} messages")
```

### Example 6: Reset Cursor and Full Re-sync

```python
# Force full re-ingestion (useful if delta sync is out of sync)
params = {
    "op": "ingest",
    "kb_id": "kb-email-archive",
    "since_hours": 168,  # Last 7 days
    "reset_cursor": True
}

result = await plugin.execute(params, context, host)
```

### Example 7: Background Feed Configuration

```json
{
  "name": "Daily Email Sync",
  "plugin_name": "outlook_mail",
  "kb_id": "kb-email-archive",
  "schedule": "0 */6 * * *",
  "params": {
    "op": "ingest"
  }
}
```

This feed will:
- Run every 6 hours
- Use delta sync automatically
- Ingest new messages and handle deletions
- Maintain cursor state between runs

## Error Handling

The plugin implements comprehensive error handling for various failure scenarios.

### Authentication Errors

| Error Code | HTTP Status | Description | Resolution |
|------------|-------------|-------------|------------|
| `auth_missing_or_insufficient_scopes` | 401 | No Microsoft access token available or authentication failed | Reconnect your Microsoft account via Connected Accounts UI |
| `insufficient_permissions` | 403 | Missing required Mail.Read scope | Grant Mail.Read permission in Azure app registration |

### Parameter Validation Errors

| Error Code | Description | Resolution |
|------------|-------------|------------|
| `missing_parameter` | kb_id is required for ingest operation | Provide kb_id parameter |
| `invalid_parameter` | Invalid op, since_hours, or max_results value | Check parameter ranges and valid values |

### API Errors

| Error Code | HTTP Status | Description | Resolution |
|------------|-------------|-------------|------------|
| `delta_token_expired` | 410 | Delta sync token expired | Plugin automatically falls back to full sync |
| `rate_limit_exceeded` | 429 | Too many requests to Graph API | Wait and retry later |
| `server_error` | 5xx | Microsoft Graph API server error | Retry later or contact Microsoft support |
| `network_error` | N/A | Network communication failure | Check network connectivity |

### Partial Success Handling

For operations that process multiple items (ingest, digest):

- Plugin continues processing remaining items when individual items fail
- Failed items are included in `skips` array with structured failure information
- Operation returns success if at least some items processed successfully
- Check `skips` array to identify and handle failed items

**Example Error Response**:

```json
{
  "status": "error",
  "error": {
    "code": "auth_missing_or_insufficient_scopes",
    "message": "Authentication failed. Please reconnect your Microsoft account.",
    "details": {
      "http_status": 401,
      "provider_message": "Invalid authentication token"
    }
  }
}
```

**Example Partial Success**:

```json
{
  "status": "success",
  "data": {
    "count": 48,
    "deleted": 2,
    "skips": [
      {
        "item_id": "AAMkAGI3...",
        "reason": "Failed to ingest message: Missing body content",
        "code": "ingestion_failed"
      }
    ]
  }
}
```

## Testing

The plugin includes comprehensive test coverage using multiple testing approaches.

### Test Structure

```text
backend/src/tests/
├── unit/plugins/
│   ├── test_outlook_mail_manifest.py           # Manifest validation
│   ├── test_outlook_mail_basic.py              # Basic functionality
│   ├── test_outlook_mail_list_operation.py     # List operation tests
│   ├── test_outlook_mail_digest_operation.py   # Digest operation tests
│   ├── test_outlook_mail_ingest_errors.py      # Ingest error handling
│   ├── test_outlook_mail_delta_sync.py         # Delta sync tests
│   ├── test_outlook_mail_error_scenarios.py    # Error handling tests
│   └── test_outlook_mail_*_properties.py       # Property-based tests (25 files)
└── integ/
    └── test_outlook_mail_integration.py        # Integration tests
```

### Running Tests

```bash
# Run all unit tests
python -m pytest backend/src/tests/unit/plugins/test_outlook_mail*.py

# Run property-based tests (100+ examples per property)
python -m pytest backend/src/tests/unit/plugins/test_outlook_mail*_property.py -v

# Run integration tests
python -m tests.integ.run_all_integration_tests --suite outlook_mail

# Run with logging
python -m tests.integ.run_all_integration_tests --suite outlook_mail --log

# Run specific test file
python -m pytest backend/src/tests/unit/plugins/test_outlook_mail_list_operation.py -v
```

### Test Coverage

- **Unit Tests**: 90%+ code coverage
- **Property Tests**: 25 correctness properties verified with 100+ examples each
- **Integration Tests**: All operations and error paths
- **Edge Cases**: All error handling branches

### Property-Based Tests

The plugin uses Hypothesis for property-based testing to verify universal correctness properties:

- Property 1: Microsoft Auth Token Resolution
- Property 2: Graph API Endpoint Correctness
- Property 3: Time-Based Message Filtering
- Property 4: Query Filter Passthrough
- Property 5: Max Results Limit Enforcement
- ... (25 properties total)

Each property is tested with 100+ randomized examples to ensure correctness across all valid inputs.

## Troubleshooting

### Common Issues

#### 1. Authentication Failed (401)

**Symptoms**: `auth_missing_or_insufficient_scopes` error

**Causes**:
- Microsoft account not connected
- OAuth token expired
- Invalid client credentials

**Solutions**:
- Reconnect Microsoft account via Connected Accounts UI
- Verify `MICROSOFT_CLIENT_ID` and `MICROSOFT_CLIENT_SECRET` are correct
- Check that redirect URI matches Azure app registration

#### 2. Insufficient Permissions (403)

**Symptoms**: `insufficient_permissions` error

**Causes**:
- Mail.Read scope not granted
- Admin consent required but not provided

**Solutions**:
- Add Mail.Read permission in Azure app registration
- Grant admin consent for the scope
- Reconnect account to refresh scopes

#### 3. Delta Token Expired (410)

**Symptoms**: `delta_token_expired` error, then automatic fallback

**Causes**:
- Delta token expired (30+ days of inactivity)
- Mailbox state changed significantly

**Solutions**:
- Plugin automatically falls back to full sync
- No action required - this is expected behavior
- Consider more frequent sync schedules to avoid expiration

#### 4. Rate Limit Exceeded (429)

**Symptoms**: `rate_limit_exceeded` error

**Causes**:
- Too many API requests in short time
- Shared tenant rate limits

**Solutions**:
- Wait and retry later (typically 1-5 minutes)
- Reduce sync frequency
- Use smaller `max_results` values
- Implement exponential backoff in feed scheduler

#### 5. No Messages Returned

**Symptoms**: Empty messages array, count = 0

**Causes**:
- No messages in time window
- Query filter too restrictive
- Mailbox empty

**Solutions**:
- Increase `since_hours` parameter
- Remove or adjust `query_filter`
- Verify messages exist in Outlook web interface
- Check that correct mailbox is connected

#### 6. Ingest Operation Fails

**Symptoms**: `missing_parameter` error

**Causes**:
- kb_id parameter not provided

**Solutions**:
- Always provide kb_id for ingest operation
- Verify knowledge base exists
- Check that user has access to knowledge base

#### 7. Partial Ingestion Success

**Symptoms**: Some messages in `skips` array

**Causes**:
- Individual messages missing required fields
- Network errors for specific messages
- Knowledge base write failures

**Solutions**:
- Review `skips` array for specific failure reasons
- Check message structure in Outlook
- Verify knowledge base is accessible
- Retry failed messages if needed

### Debug Mode

Enable debug mode to get detailed operational diagnostics:

```python
params = {
    "op": "list",
    "debug": True
}
```

Debug output includes:
- Time window calculations
- API endpoint URLs
- Filter expressions
- Message counts at each stage
- Cursor operations
- Error details

### Logging

The plugin logs operational details using Python's logging module:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

Log messages include:
- API requests and responses
- Delta sync operations
- Cursor updates
- Error details with stack traces

### Support Resources

- **Microsoft Graph API Documentation**: <https://learn.microsoft.com/en-us/graph/api/resources/mail-api-overview>
- **OAuth Setup Guide**: `shu/docs/onboarding/microsoft-365-oauth-setup.md`
- **Plugin Contract**: `shu/docs/contracts/PLUGIN_CONTRACT.md`
- **Testing Documentation**: `shu/docs/policies/TESTING.md`

## API Reference

### Microsoft Graph API Endpoints

The plugin uses the following Microsoft Graph API v1.0 endpoints:

- `GET /me/mailFolders/inbox/messages` - List inbox messages
- `GET /me/mailFolders/inbox/messages/delta` - Delta query for incremental sync
- `GET /me/messages/{message-id}` - Get full message content

### OData Query Parameters

- `$select` - Specify fields to return
- `$filter` - Filter messages by criteria
- `$top` - Limit number of results
- `$orderby` - Sort results
- `$skip` - Skip results (pagination)

### Required Scopes

- `https://graph.microsoft.com/Mail.Read` - Read user mail
- `offline_access` - Maintain refresh token (automatically added)

## Contributing

When contributing to the Outlook Mail plugin:

1. Follow the [Shu Coding Standards](../../docs/policies/DEVELOPMENT_STANDARDS.md)
2. Write tests for all new functionality (unit + property + integration)
3. Update this README with any new features or changes
4. Ensure all tests pass before submitting
5. Follow the plugin contract defined in `PLUGIN_CONTRACT.md`

## License

This plugin is part of the Shu project and follows the same license terms.

## Version History

- **v1.0.0** (2024-01): Initial release
  - List, digest, and ingest operations
  - Delta sync support
  - Microsoft Graph API integration
  - Comprehensive test coverage
