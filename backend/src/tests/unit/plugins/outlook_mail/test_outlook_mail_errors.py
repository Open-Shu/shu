"""Error handling tests for Outlook Mail plugin.

Tests HTTP errors, network errors, diagnostics, and skips array.

Note: The Outlook Mail plugin lets HttpRequestFailed exceptions bubble up to the
executor, which converts them to structured PluginResult.err() responses. These
tests verify that behavior by wrapping plugin execution with executor-like error
handling, simulating the full execution flow.
"""
import pytest
from conftest import HttpRequestFailed, wrap_graph_response


def _executor_error_handler(e: HttpRequestFailed):
    """Simulate executor's HttpRequestFailed handling (from executor.py lines 642-661).
    
    This mirrors the actual executor behavior so we can test the full error flow.
    """
    from plugins.shu_outlook_mail.plugin import _Result

    details = {
        "status_code": e.status_code,
        "url": e.url,
        "provider_message": e.provider_message,
        "is_retryable": e.is_retryable,
    }
    if e.provider_error_code:
        details["provider_error_code"] = e.provider_error_code
    if e.retry_after_seconds is not None:
        details["retry_after_seconds"] = e.retry_after_seconds

    message = f"Provider HTTP error ({e.status_code}): {e.provider_message}" if e.provider_message else f"Provider HTTP error ({e.status_code})"
    return _Result.err(message=message, code=e.error_category, details=details)


async def execute_with_error_handling(plugin, params, context, host):
    """Execute plugin with executor-like error handling for HttpRequestFailed."""
    try:
        return await plugin.execute(params, context, host)
    except HttpRequestFailed as e:
        return _executor_error_handler(e)


class TestHttpErrors:
    """Test HTTP error handling.
    
    The plugin lets HttpRequestFailed bubble up to the executor, which converts
    it to structured errors using error_category. These tests use execute_with_error_handling
    to simulate the full execution flow.
    """

    @pytest.mark.asyncio
    async def test_401_returns_auth_error(self, plugin, mock_host):
        """Test HTTP 401 returns auth_error (executor converts HttpRequestFailed)."""
        mock_host.http.fetch.side_effect = HttpRequestFailed(
            status_code=401,
            url="https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
            body={"error": {"message": "Invalid authentication token"}}
        )

        result = await execute_with_error_handling(plugin, {"op": "list"}, None, mock_host)

        assert result.status == "error"
        assert result.error["code"] == "auth_error"
        assert "401" in result.error["message"]
        assert result.error["details"]["status_code"] == 401

    @pytest.mark.asyncio
    async def test_403_returns_forbidden_error(self, plugin, mock_host):
        """Test HTTP 403 returns forbidden error (executor converts HttpRequestFailed)."""
        mock_host.http.fetch.side_effect = HttpRequestFailed(
            status_code=403,
            url="https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
            body={"error": {"message": "Insufficient permissions to access mailbox"}}
        )

        result = await execute_with_error_handling(plugin, {"op": "list"}, None, mock_host)

        assert result.status == "error"
        assert result.error["code"] == "forbidden"
        assert "403" in result.error["message"]
        assert result.error["details"]["status_code"] == 403

    @pytest.mark.asyncio
    async def test_429_returns_rate_limited_error(self, plugin, mock_host):
        """Test HTTP 429 returns rate_limited error (executor converts HttpRequestFailed)."""
        mock_host.http.fetch.side_effect = HttpRequestFailed(
            status_code=429,
            url="https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
            body={"error": {"message": "Too many requests"}}
        )

        result = await execute_with_error_handling(plugin, {"op": "list"}, None, mock_host)

        assert result.status == "error"
        assert result.error["code"] == "rate_limited"
        assert "429" in result.error["message"]
        assert result.error["details"]["status_code"] == 429

    @pytest.mark.asyncio
    async def test_500_returns_server_error(self, plugin, mock_host):
        """Test HTTP 500 returns server_error (executor converts HttpRequestFailed)."""
        mock_host.http.fetch.side_effect = HttpRequestFailed(
            status_code=500,
            url="https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
            body={"error": {"message": "Internal server error"}}
        )

        result = await execute_with_error_handling(plugin, {"op": "list"}, None, mock_host)

        assert result.status == "error"
        assert result.error["code"] == "server_error"
        assert "500" in result.error["message"]

    @pytest.mark.asyncio
    async def test_503_returns_server_error(self, plugin, mock_host):
        """Test HTTP 503 returns server_error (executor converts HttpRequestFailed)."""
        mock_host.http.fetch.side_effect = HttpRequestFailed(
            status_code=503,
            url="https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
            body={"error": {"message": "Service temporarily unavailable"}}
        )

        result = await execute_with_error_handling(plugin, {"op": "list"}, None, mock_host)

        assert result.status == "error"
        assert result.error["code"] == "server_error"
        assert "503" in result.error["message"]

    @pytest.mark.asyncio
    async def test_error_details_include_provider_message(self, plugin, mock_host):
        """Test error details include provider_message from Graph API."""
        mock_host.http.fetch.side_effect = HttpRequestFailed(
            status_code=401,
            url="https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
            body={"error": {"message": "Token expired or invalid"}}
        )

        result = await execute_with_error_handling(plugin, {"op": "list"}, None, mock_host)

        assert result.status == "error"
        assert "details" in result.error
        assert "provider_message" in result.error["details"]
        assert result.error["details"]["provider_message"] == "Token expired or invalid"


class TestNetworkErrors:
    """Test network error handling.
    
    Generic exceptions (not HttpRequestFailed) bubble up to the executor which
    converts them to plugin_execute_error. These tests verify that behavior.
    """

    @pytest.mark.asyncio
    async def test_network_error_bubbles_up_as_plugin_error(self, plugin, mock_host):
        """Test network errors bubble up and executor converts to plugin_execute_error."""
        mock_host.http.fetch.side_effect = Exception("Connection timeout")

        # The plugin lets this exception bubble up
        # The executor would catch it and convert to plugin_execute_error
        with pytest.raises(Exception) as exc_info:
            await plugin.execute({"op": "list"}, None, mock_host)

        assert "Connection timeout" in str(exc_info.value)


class TestDiagnostics:
    """Test debug diagnostics."""

    @pytest.mark.asyncio
    async def test_diagnostics_included_with_debug_flag(self, plugin, mock_host):
        """Test diagnostics included when debug=True."""
        mock_host.http.fetch.return_value = wrap_graph_response({
            "value": [
                {
                    "id": "msg1",
                    "subject": "Test",
                    "from": {"emailAddress": {"address": "test@example.com"}},
                    "to": [],
                    "receivedDateTime": "2024-01-15T10:00:00Z",
                    "bodyPreview": "Test"
                }
            ],
            "@odata.nextLink": None
        })

        result = await plugin.execute({"op": "list", "debug": True}, None, mock_host)

        assert result.status == "success"
        assert "diagnostics" in result.data
        assert isinstance(result.data["diagnostics"], list)
        assert len(result.data["diagnostics"]) > 0

    @pytest.mark.asyncio
    async def test_diagnostics_not_included_without_debug_flag(self, plugin, mock_host):
        """Test diagnostics not included when debug=False (default)."""
        mock_host.http.fetch.return_value = wrap_graph_response({
            "value": [],
            "@odata.nextLink": None
        })

        result = await plugin.execute({"op": "list"}, None, mock_host)

        assert result.status == "success"
        assert "diagnostics" not in result.data

    @pytest.mark.asyncio
    async def test_diagnostics_for_digest_operation(self, plugin, mock_host):
        """Test diagnostics included for digest operation."""
        mock_host.http.fetch.return_value = wrap_graph_response({
            "value": [
                {
                    "id": "msg1",
                    "subject": "Test",
                    "from": {"emailAddress": {"address": "test@example.com", "name": "Test"}},
                    "to": [],
                    "receivedDateTime": "2024-01-15T10:00:00Z",
                    "bodyPreview": "Test"
                }
            ],
            "@odata.nextLink": None
        })

        result = await plugin.execute({"op": "digest", "debug": True}, None, mock_host)

        assert result.status == "success"
        assert "diagnostics" in result.data

    @pytest.mark.asyncio
    async def test_diagnostics_for_ingest_operation(self, plugin, mock_host):
        """Test diagnostics included for ingest operation."""
        mock_host.http.fetch.side_effect = [
            wrap_graph_response({"value": [], "@odata.nextLink": None}),
            wrap_graph_response({"value": [], "@odata.deltaLink": "https://graph.microsoft.com/delta?token=abc"})
        ]

        result = await plugin.execute(
            {"op": "ingest", "kb_id": "test-kb", "debug": True},
            None,
            mock_host
        )

        assert result.status == "success"
        assert "diagnostics" in result.data


class TestSkipsArray:
    """Test skips array for failed items during ingest."""

    @pytest.mark.asyncio
    async def test_skips_includes_failed_items(self, plugin, mock_host):
        """Test skips array includes failed ingestion items."""
        mock_host.http.fetch.side_effect = [
            # List messages
            wrap_graph_response({"value": [{"id": "msg1"}, {"id": "msg2"}], "@odata.nextLink": None}),
            # Get delta token
            wrap_graph_response({"value": [], "@odata.deltaLink": "https://graph.microsoft.com/delta?token=abc"}),
            # Fetch full message 1
            wrap_graph_response({
                "id": "msg1",
                "subject": "Test 1",
                "from": {"emailAddress": {"name": "J", "address": "j@e.com"}},
                "toRecipients": [],
                "ccRecipients": [],
                "bccRecipients": [],
                "receivedDateTime": "2024-01-15T10:00:00Z",
                "body": {"contentType": "text", "content": "Body 1"}
            }),
            # Fetch full message 2
            wrap_graph_response({
                "id": "msg2",
                "subject": "Test 2",
                "from": {"emailAddress": {"name": "J", "address": "j@e.com"}},
                "toRecipients": [],
                "ccRecipients": [],
                "bccRecipients": [],
                "receivedDateTime": "2024-01-15T11:00:00Z",
                "body": {"contentType": "text", "content": "Body 2"}
            })
        ]

        # Make first ingest fail
        call_count = [0]
        async def ingest_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Database error")
            return {"id": "ko_id"}

        mock_host.kb.ingest_email.side_effect = ingest_side_effect

        result = await plugin.execute({"op": "ingest", "kb_id": "test-kb"}, None, mock_host)

        assert result.status == "success"
        assert "skips" in result.data
        assert len(result.data["skips"]) == 1
        assert result.data["skips"][0]["item_id"] == "msg1"
        assert result.data["skips"][0]["code"] == "ingestion_failed"
        assert "Database error" in result.data["skips"][0]["reason"]

    @pytest.mark.asyncio
    async def test_skips_has_structured_format(self, plugin, mock_host):
        """Test skips entries have item_id, reason, and code fields."""
        # Setup: return a message with body included (no separate fetch needed after N+1 fix)
        mock_host.http.fetch.side_effect = [
            # List messages with body included
            wrap_graph_response({
                "value": [{
                    "id": "msg1",
                    "subject": "Test",
                    "from": {"emailAddress": {"name": "J", "address": "j@e.com"}},
                    "toRecipients": [],
                    "ccRecipients": [],
                    "bccRecipients": [],
                    "receivedDateTime": "2024-01-15T10:00:00Z",
                    "body": {"contentType": "text", "content": "Body"}
                }],
                "@odata.nextLink": None
            }),
            # Delta query to get initial token
            wrap_graph_response({"value": [], "@odata.deltaLink": "https://graph.microsoft.com/delta?token=abc"})
        ]

        # Make ingestion fail to trigger skip
        mock_host.kb.ingest_email.side_effect = Exception("Message not found in mailbox")

        result = await plugin.execute({"op": "ingest", "kb_id": "test-kb"}, None, mock_host)

        assert result.status == "success"
        assert "skips" in result.data
        assert len(result.data["skips"]) == 1

        skip = result.data["skips"][0]
        assert "item_id" in skip
        assert "reason" in skip
        assert "code" in skip
        assert skip["item_id"] == "msg1"
        assert skip["code"] == "ingestion_failed"

    @pytest.mark.asyncio
    async def test_no_skips_when_all_succeed(self, plugin, mock_host):
        """Test skips not included when all items succeed."""
        mock_host.http.fetch.side_effect = [
            wrap_graph_response({"value": [{"id": "msg1"}], "@odata.nextLink": None}),
            wrap_graph_response({"value": [], "@odata.deltaLink": "https://graph.microsoft.com/delta?token=abc"}),
            wrap_graph_response({
                "id": "msg1",
                "subject": "Test",
                "from": {"emailAddress": {"name": "J", "address": "j@e.com"}},
                "toRecipients": [],
                "ccRecipients": [],
                "bccRecipients": [],
                "receivedDateTime": "2024-01-15T10:00:00Z",
                "body": {"contentType": "text", "content": "Body"}
            })
        ]
        mock_host.kb.ingest_email.return_value = {"id": "ko_id"}

        result = await plugin.execute({"op": "ingest", "kb_id": "test-kb"}, None, mock_host)

        assert result.status == "success"
        assert result.data["count"] == 1
        assert "skips" not in result.data


class TestCursorErrors:
    """Test cursor-related error handling."""

    @pytest.mark.asyncio
    async def test_cursor_update_failure_is_best_effort(self, plugin, mock_host):
        """Test cursor update failure doesn't fail the operation."""
        mock_host.cursor.get.return_value = "https://graph.microsoft.com/delta?token=old"
        mock_host.cursor.set.side_effect = Exception("Cursor storage failed")
        mock_host.http.fetch.side_effect = [
            wrap_graph_response({
                "value": [
                    {
                        "id": "msg1",
                        "subject": "New",
                        "from": {"emailAddress": {"name": "J", "address": "j@e.com"}},
                        "receivedDateTime": "2024-01-15T10:00:00Z"
                    }
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/delta?token=new"
            }),
            wrap_graph_response({
                "id": "msg1",
                "subject": "New",
                "from": {"emailAddress": {"name": "J", "address": "j@e.com"}},
                "toRecipients": [],
                "ccRecipients": [],
                "bccRecipients": [],
                "receivedDateTime": "2024-01-15T10:00:00Z",
                "body": {"contentType": "text", "content": "Body"}
            })
        ]
        mock_host.kb.ingest_email.return_value = {"id": "ko_id"}

        result = await plugin.execute({"op": "ingest", "kb_id": "test-kb"}, None, mock_host)

        # Operation should succeed despite cursor update failure
        assert result.status == "success"
        assert result.data["count"] == 1
