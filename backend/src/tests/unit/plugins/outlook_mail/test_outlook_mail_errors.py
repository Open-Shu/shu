"""Error handling tests for Outlook Mail plugin.

Tests HTTP errors, network errors, diagnostics, and skips array.
"""
import pytest
from conftest import MockHttpRequestFailed


class TestHttpErrors:
    """Test HTTP error handling."""

    @pytest.mark.asyncio
    async def test_401_returns_auth_error(self, plugin, mock_host):
        """Test HTTP 401 returns auth_missing_or_insufficient_scopes error."""
        mock_host.http.fetch.side_effect = MockHttpRequestFailed(
            status_code=401,
            url="https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
            body={"error": {"message": "Invalid authentication token"}}
        )
        
        result = await plugin.execute({"op": "list"}, None, mock_host)
        
        assert result.status == "error"
        assert result.error["code"] == "auth_missing_or_insufficient_scopes"
        assert "Authentication failed" in result.error["message"]
        assert result.error["details"]["http_status"] == 401

    @pytest.mark.asyncio
    async def test_403_returns_permission_error(self, plugin, mock_host):
        """Test HTTP 403 returns insufficient_permissions error."""
        mock_host.http.fetch.return_value = {
            "status_code": 403,
            "error": {"message": "Insufficient permissions to access mailbox"}
        }
        
        result = await plugin.execute({"op": "list"}, None, mock_host)
        
        assert result.status == "error"
        assert result.error["code"] == "insufficient_permissions"
        assert "Insufficient permissions" in result.error["message"]
        assert result.error["details"]["http_status"] == 403

    @pytest.mark.asyncio
    async def test_429_returns_rate_limit_error(self, plugin, mock_host):
        """Test HTTP 429 returns rate_limit_exceeded error."""
        mock_host.http.fetch.return_value = {
            "status_code": 429,
            "error": {"message": "Too many requests"}
        }
        
        result = await plugin.execute({"op": "list"}, None, mock_host)
        
        assert result.status == "error"
        assert result.error["code"] == "rate_limit_exceeded"
        assert "Rate limit exceeded" in result.error["message"]
        assert result.error["details"]["http_status"] == 429

    @pytest.mark.asyncio 
    async def test_500_returns_server_error(self, plugin, mock_host):
        """Test HTTP 500 returns server_error."""
        mock_host.http.fetch.return_value = {
            "status_code": 500,
            "error": {"message": "Internal server error"}
        }
        
        result = await plugin.execute({"op": "list"}, None, mock_host)
        
        assert result.status == "error"
        assert result.error["code"] == "server_error"
        assert "Server error: 500" in result.error["message"]

    @pytest.mark.asyncio
    async def test_503_returns_server_error(self, plugin, mock_host):
        """Test HTTP 503 returns server_error."""
        mock_host.http.fetch.return_value = {
            "status_code": 503,
            "error": {"message": "Service temporarily unavailable"}
        }
        
        result = await plugin.execute({"op": "list"}, None, mock_host)
        
        assert result.status == "error"
        assert result.error["code"] == "server_error"
        assert "Server error: 503" in result.error["message"]

    @pytest.mark.asyncio
    async def test_error_details_include_provider_message(self, plugin, mock_host):
        """Test error details include provider_message from Graph API."""
        mock_host.http.fetch.side_effect = MockHttpRequestFailed(
            status_code=401,
            url="https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
            body={"error": {"message": "Token expired or invalid"}}
        )
        
        result = await plugin.execute({"op": "list"}, None, mock_host)
        
        assert result.status == "error"
        assert "details" in result.error
        assert "provider_message" in result.error["details"]
        assert result.error["details"]["provider_message"] == "Token expired or invalid"


class TestNetworkErrors:
    """Test network error handling."""

    @pytest.mark.asyncio
    async def test_network_error_returns_appropriate_error(self, plugin, mock_host):
        """Test network errors return network_error code."""
        mock_host.http.fetch.side_effect = Exception("Connection timeout")
        
        result = await plugin.execute({"op": "list"}, None, mock_host)
        
        assert result.status == "error"
        assert result.error["code"] == "network_error"
        assert "Network error:" in result.error["message"]
        assert "Connection timeout" in result.error["message"]


class TestDiagnostics:
    """Test debug diagnostics."""

    @pytest.mark.asyncio
    async def test_diagnostics_included_with_debug_flag(self, plugin, mock_host):
        """Test diagnostics included when debug=True."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
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
        }
        
        result = await plugin.execute({"op": "list", "debug": True}, None, mock_host)
        
        assert result.status == "success"
        assert "diagnostics" in result.data
        assert isinstance(result.data["diagnostics"], list)
        assert len(result.data["diagnostics"]) > 0

    @pytest.mark.asyncio
    async def test_diagnostics_not_included_without_debug_flag(self, plugin, mock_host):
        """Test diagnostics not included when debug=False (default)."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [],
            "@odata.nextLink": None
        }
        
        result = await plugin.execute({"op": "list"}, None, mock_host)
        
        assert result.status == "success"
        assert "diagnostics" not in result.data

    @pytest.mark.asyncio
    async def test_diagnostics_for_digest_operation(self, plugin, mock_host):
        """Test diagnostics included for digest operation."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
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
        }
        
        result = await plugin.execute({"op": "digest", "debug": True}, None, mock_host)
        
        assert result.status == "success"
        assert "diagnostics" in result.data

    @pytest.mark.asyncio
    async def test_diagnostics_for_ingest_operation(self, plugin, mock_host):
        """Test diagnostics included for ingest operation."""
        mock_host.http.fetch.side_effect = [
            {"status_code": 200, "value": [], "@odata.nextLink": None},
            {"status_code": 200, "value": [], "@odata.deltaLink": "https://graph.microsoft.com/delta?token=abc"}
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
            {"status_code": 200, "value": [{"id": "msg1"}, {"id": "msg2"}], "@odata.nextLink": None},
            # Get delta token
            {"status_code": 200, "value": [], "@odata.deltaLink": "https://graph.microsoft.com/delta?token=abc"},
            # Fetch full message 1
            {
                "status_code": 200,
                "id": "msg1",
                "subject": "Test 1",
                "from": {"emailAddress": {"name": "J", "address": "j@e.com"}},
                "toRecipients": [],
                "ccRecipients": [],
                "bccRecipients": [],
                "receivedDateTime": "2024-01-15T10:00:00Z",
                "body": {"contentType": "text", "content": "Body 1"}
            },
            # Fetch full message 2
            {
                "status_code": 200,
                "id": "msg2",
                "subject": "Test 2",
                "from": {"emailAddress": {"name": "J", "address": "j@e.com"}},
                "toRecipients": [],
                "ccRecipients": [],
                "bccRecipients": [],
                "receivedDateTime": "2024-01-15T11:00:00Z",
                "body": {"contentType": "text", "content": "Body 2"}
            }
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
        mock_host.http.fetch.side_effect = [
            {"status_code": 200, "value": [{"id": "msg1"}], "@odata.nextLink": None},
            {"status_code": 200, "value": [], "@odata.deltaLink": "https://graph.microsoft.com/delta?token=abc"},
            {"status_code": 404, "error": {"message": "Message not found"}}
        ]
        
        result = await plugin.execute({"op": "ingest", "kb_id": "test-kb"}, None, mock_host)
        
        assert result.status == "success"
        assert "skips" in result.data
        assert len(result.data["skips"]) == 1
        
        skip = result.data["skips"][0]
        assert "item_id" in skip
        assert "reason" in skip
        assert "code" in skip

    @pytest.mark.asyncio
    async def test_no_skips_when_all_succeed(self, plugin, mock_host):
        """Test skips not included when all items succeed."""
        mock_host.http.fetch.side_effect = [
            {"status_code": 200, "value": [{"id": "msg1"}], "@odata.nextLink": None},
            {"status_code": 200, "value": [], "@odata.deltaLink": "https://graph.microsoft.com/delta?token=abc"},
            {
                "status_code": 200,
                "id": "msg1",
                "subject": "Test",
                "from": {"emailAddress": {"name": "J", "address": "j@e.com"}},
                "toRecipients": [],
                "ccRecipients": [],
                "bccRecipients": [],
                "receivedDateTime": "2024-01-15T10:00:00Z",
                "body": {"contentType": "text", "content": "Body"}
            }
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
            {
                "status_code": 200,
                "value": [
                    {
                        "id": "msg1",
                        "subject": "New",
                        "from": {"emailAddress": {"name": "J", "address": "j@e.com"}},
                        "receivedDateTime": "2024-01-15T10:00:00Z"
                    }
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/delta?token=new"
            },
            {
                "status_code": 200,
                "id": "msg1",
                "subject": "New",
                "from": {"emailAddress": {"name": "J", "address": "j@e.com"}},
                "toRecipients": [],
                "ccRecipients": [],
                "bccRecipients": [],
                "receivedDateTime": "2024-01-15T10:00:00Z",
                "body": {"contentType": "text", "content": "Body"}
            }
        ]
        mock_host.kb.ingest_email.return_value = {"id": "ko_id"}
        
        result = await plugin.execute({"op": "ingest", "kb_id": "test-kb"}, None, mock_host)
        
        # Operation should succeed despite cursor update failure
        assert result.status == "success"
        assert result.data["count"] == 1
