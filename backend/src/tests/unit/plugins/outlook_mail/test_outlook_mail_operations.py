"""Operation tests for Outlook Mail plugin.

Tests list, digest, ingest, and delta sync operation behavior.
"""
import pytest
from urllib.parse import unquote


class TestListOperation:
    """Test list operation behavior."""

    @pytest.mark.asyncio
    async def test_fetches_from_inbox_endpoint(self, plugin, mock_host):
        """Test list fetches from /me/mailFolders/inbox/messages endpoint."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [],
            "@odata.nextLink": None
        }
        
        result = await plugin.execute({"op": "list"}, None, mock_host)
        
        assert result.status == "success"
        call_args = mock_host.http.fetch.call_args
        url = call_args.kwargs['url']
        assert "/me/mailFolders/inbox/messages" in url

    @pytest.mark.asyncio
    async def test_includes_authorization_header(self, plugin, mock_host):
        """Test Authorization header contains Bearer token."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [],
            "@odata.nextLink": None
        }
        
        result = await plugin.execute({"op": "list"}, None, mock_host)
        
        assert result.status == "success"
        call_args = mock_host.http.fetch.call_args
        headers = call_args.kwargs['headers']
        assert headers["Authorization"] == "Bearer test_token_123"

    @pytest.mark.asyncio
    async def test_requests_metadata_fields(self, plugin, mock_host):
        """Test $select parameter includes required message fields."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [],
            "@odata.nextLink": None
        }
        
        await plugin.execute({"op": "list"}, None, mock_host)
        
        call_args = mock_host.http.fetch.call_args
        url = call_args.kwargs['url']
        assert "$select=" in url
        assert "id" in url
        assert "subject" in url
        assert "from" in url
        assert "receivedDateTime" in url
        assert "bodyPreview" in url

    @pytest.mark.asyncio
    async def test_applies_since_hours_filter(self, plugin, mock_host):
        """Test since_hours applies receivedDateTime filter."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [],
            "@odata.nextLink": None
        }
        
        await plugin.execute({"op": "list", "since_hours": 24}, None, mock_host)
        
        call_args = mock_host.http.fetch.call_args
        url = call_args.kwargs['url']
        assert "$filter=" in url
        assert "receivedDateTime" in url

    @pytest.mark.asyncio
    async def test_applies_query_filter(self, plugin, mock_host):
        """Test query_filter is passed through to $filter."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [],
            "@odata.nextLink": None
        }
        
        await plugin.execute(
            {"op": "list", "query_filter": "from/emailAddress/address eq 'test@example.com'"},
            None,
            mock_host
        )
        
        call_args = mock_host.http.fetch.call_args
        url = unquote(call_args.kwargs['url'])
        assert "from/emailAddress/address" in url

    @pytest.mark.asyncio
    async def test_applies_max_results(self, plugin, mock_host):
        """Test max_results sets $top parameter."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [],
            "@odata.nextLink": None
        }
        
        await plugin.execute({"op": "list", "max_results": 10}, None, mock_host)
        
        call_args = mock_host.http.fetch.call_args
        url = call_args.kwargs['url']
        assert "$top=10" in url

    @pytest.mark.asyncio
    async def test_returns_messages_with_count(self, plugin, mock_host):
        """Test list returns messages array and count."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [
                {"id": "msg1", "subject": "Test 1"},
                {"id": "msg2", "subject": "Test 2"}
            ],
            "@odata.nextLink": None
        }
        
        result = await plugin.execute({"op": "list"}, None, mock_host)
        
        assert result.status == "success"
        assert "messages" in result.data
        assert len(result.data["messages"]) == 2
        assert result.data["count"] == 2

    @pytest.mark.asyncio
    async def test_handles_pagination(self, plugin, mock_host):
        """Test list follows @odata.nextLink for pagination."""
        mock_host.http.fetch.side_effect = [
            {
                "status_code": 200,
                "value": [{"id": "msg1"}, {"id": "msg2"}],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skip=2"
            },
            {
                "status_code": 200,
                "value": [{"id": "msg3"}],
                "@odata.nextLink": None
            }
        ]
        
        result = await plugin.execute({"op": "list"}, None, mock_host)
        
        assert result.status == "success"
        assert len(result.data["messages"]) == 3
        assert mock_host.http.fetch.call_count == 2

    @pytest.mark.asyncio
    async def test_respects_max_results_across_pages(self, plugin, mock_host):
        """Test pagination stops when max_results is reached."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [{"id": f"msg{i}"} for i in range(10)],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skip=10"
        }
        
        result = await plugin.execute({"op": "list", "max_results": 5}, None, mock_host)
        
        assert result.status == "success"
        assert len(result.data["messages"]) == 5
        assert mock_host.http.fetch.call_count == 1

    @pytest.mark.asyncio
    async def test_orders_by_received_date_desc(self, plugin, mock_host):
        """Test messages are ordered by receivedDateTime descending."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [],
            "@odata.nextLink": None
        }
        
        await plugin.execute({"op": "list"}, None, mock_host)
        
        call_args = mock_host.http.fetch.call_args
        url = call_args.kwargs['url']
        assert "$orderby=receivedDateTime" in url
        assert "desc" in url


class TestDigestOperation:
    """Test digest operation behavior."""

    @pytest.mark.asyncio
    async def test_creates_email_digest_ko(self, plugin, mock_host):
        """Test digest creates KO with type email_digest."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [
                {
                    "id": "msg1",
                    "subject": "Test Subject",
                    "from": {"emailAddress": {"name": "John", "address": "john@example.com"}},
                    "to": [],
                    "receivedDateTime": "2024-01-15T10:00:00Z",
                    "bodyPreview": "Test"
                }
            ],
            "@odata.nextLink": None
        }
        
        result = await plugin.execute({"op": "digest"}, None, mock_host)
        
        assert result.status == "success"
        assert result.data["ko"]["type"] == "email_digest"
        assert "title" in result.data["ko"]
        assert "content" in result.data["ko"]
        assert "attributes" in result.data["ko"]

    @pytest.mark.asyncio
    async def test_analyzes_top_senders(self, plugin, mock_host):
        """Test digest identifies top senders with message counts."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [
                {"id": "1", "subject": "S1", "from": {"emailAddress": {"name": "John", "address": "john@example.com"}}, "to": [], "receivedDateTime": "2024-01-15T10:00:00Z", "bodyPreview": ""},
                {"id": "2", "subject": "S2", "from": {"emailAddress": {"name": "John", "address": "john@example.com"}}, "to": [], "receivedDateTime": "2024-01-15T11:00:00Z", "bodyPreview": ""},
                {"id": "3", "subject": "S3", "from": {"emailAddress": {"name": "Jane", "address": "jane@example.com"}}, "to": [], "receivedDateTime": "2024-01-15T12:00:00Z", "bodyPreview": ""}
            ],
            "@odata.nextLink": None
        }
        
        result = await plugin.execute({"op": "digest"}, None, mock_host)
        
        top_senders = result.data["ko"]["attributes"]["top_senders"]
        assert len(top_senders) == 2
        assert top_senders[0]["email"] == "john@example.com"
        assert top_senders[0]["count"] == 2
        assert top_senders[1]["count"] == 1

    @pytest.mark.asyncio
    async def test_extracts_recent_subjects(self, plugin, mock_host):
        """Test digest extracts recent message subjects."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [
                {"id": "1", "subject": "Subject A", "from": {"emailAddress": {"name": "Test", "address": "test@example.com"}}, "to": [], "receivedDateTime": "2024-01-15T10:00:00Z", "bodyPreview": ""},
                {"id": "2", "subject": "Subject B", "from": {"emailAddress": {"name": "Test", "address": "test@example.com"}}, "to": [], "receivedDateTime": "2024-01-15T11:00:00Z", "bodyPreview": ""}
            ],
            "@odata.nextLink": None
        }
        
        result = await plugin.execute({"op": "digest"}, None, mock_host)
        
        recent_subjects = result.data["ko"]["attributes"]["recent_subjects"]
        assert "Subject A" in recent_subjects
        assert "Subject B" in recent_subjects

    @pytest.mark.asyncio
    async def test_includes_window_metadata(self, plugin, mock_host):
        """Test digest includes time window metadata."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [],
            "@odata.nextLink": None
        }
        
        result = await plugin.execute({"op": "digest", "since_hours": 48}, None, mock_host)
        
        window = result.data["window"]
        assert "since" in window
        assert "until" in window
        assert window["hours"] == 48

    @pytest.mark.asyncio
    async def test_writes_to_kb_when_kb_id_provided(self, plugin, mock_host):
        """Test digest writes KO to KB when kb_id is provided."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [],
            "@odata.nextLink": None
        }
        
        result = await plugin.execute({"op": "digest", "kb_id": "test-kb"}, None, mock_host)
        
        assert result.status == "success"
        mock_host.kb.write_ko.assert_called_once()
        call_args = mock_host.kb.write_ko.call_args
        assert call_args.kwargs.get('kb_id') == "test-kb" or (call_args.args and call_args.args[0] == "test-kb")

    @pytest.mark.asyncio
    async def test_no_kb_write_without_kb_id(self, plugin, mock_host):
        """Test digest does NOT write to KB when kb_id is not provided."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [],
            "@odata.nextLink": None
        }
        
        result = await plugin.execute({"op": "digest"}, None, mock_host)
        
        assert result.status == "success"
        mock_host.kb.write_ko.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_zero_messages(self, plugin, mock_host):
        """Test digest handles zero messages gracefully."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [],
            "@odata.nextLink": None
        }
        
        result = await plugin.execute({"op": "digest"}, None, mock_host)
        
        assert result.status == "success"
        assert result.data["count"] == 0
        assert len(result.data["ko"]["attributes"]["top_senders"]) == 0
        assert len(result.data["ko"]["attributes"]["recent_subjects"]) == 0

    @pytest.mark.asyncio
    async def test_limits_top_senders_to_10(self, plugin, mock_host):
        """Test digest limits top senders to 10."""
        messages = [
            {"id": f"msg{i}", "subject": f"Subject {i}", "from": {"emailAddress": {"name": f"User {i}", "address": f"user{i}@example.com"}}, "to": [], "receivedDateTime": "2024-01-15T10:00:00Z", "bodyPreview": ""}
            for i in range(15)
        ]
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": messages,
            "@odata.nextLink": None
        }
        
        result = await plugin.execute({"op": "digest"}, None, mock_host)
        
        assert len(result.data["ko"]["attributes"]["top_senders"]) == 10

    @pytest.mark.asyncio
    async def test_limits_recent_subjects_to_20(self, plugin, mock_host):
        """Test digest limits recent subjects to 20."""
        messages = [
            {"id": f"msg{i}", "subject": f"Subject {i}", "from": {"emailAddress": {"name": "User", "address": "user@example.com"}}, "to": [], "receivedDateTime": "2024-01-15T10:00:00Z", "bodyPreview": ""}
            for i in range(25)
        ]
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": messages,
            "@odata.nextLink": None
        }
        
        result = await plugin.execute({"op": "digest"}, None, mock_host)
        
        assert len(result.data["ko"]["attributes"]["recent_subjects"]) == 20


class TestIngestOperation:
    """Test ingest operation behavior."""

    @pytest.mark.asyncio
    async def test_fetches_and_ingests_messages(self, plugin, mock_host):
        """Test ingest fetches full messages and ingests them."""
        mock_host.http.fetch.side_effect = [
            # List messages
            {
                "status_code": 200,
                "value": [{"id": "msg1"}],
                "@odata.nextLink": None
            },
            # Get delta token
            {
                "status_code": 200,
                "value": [],
                "@odata.deltaLink": "https://graph.microsoft.com/delta?token=abc"
            },
            # Fetch full message
            {
                "status_code": 200,
                "id": "msg1",
                "subject": "Test",
                "from": {"emailAddress": {"name": "John", "address": "john@example.com"}},
                "toRecipients": [],
                "ccRecipients": [],
                "bccRecipients": [],
                "receivedDateTime": "2024-01-15T10:00:00Z",
                "body": {"contentType": "text", "content": "Body"}
            }
        ]
        
        result = await plugin.execute({"op": "ingest", "kb_id": "test-kb"}, None, mock_host)
        
        assert result.status == "success"
        assert result.data["count"] == 1
        mock_host.kb.ingest_email.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_ingestion_and_deletion_counts(self, plugin, mock_host):
        """Test ingest returns both count and deleted fields."""
        mock_host.http.fetch.side_effect = [
            {"status_code": 200, "value": [], "@odata.nextLink": None},
            {"status_code": 200, "value": [], "@odata.deltaLink": "https://graph.microsoft.com/delta?token=abc"}
        ]
        
        result = await plugin.execute({"op": "ingest", "kb_id": "test-kb"}, None, mock_host)
        
        assert result.status == "success"
        assert "count" in result.data
        assert "deleted" in result.data


class TestDeltaSyncBehavior:
    """Test delta sync behavior for incremental updates."""

    @pytest.mark.asyncio
    async def test_retrieves_cursor_before_processing(self, plugin, mock_host):
        """Test plugin retrieves cursor via host.cursor.get(kb_id)."""
        mock_host.cursor.get.return_value = None
        mock_host.http.fetch.side_effect = [
            {"status_code": 200, "value": [], "@odata.nextLink": None},
            {"status_code": 200, "value": [], "@odata.deltaLink": "https://graph.microsoft.com/delta?token=xyz"}
        ]
        
        await plugin.execute({"op": "ingest", "kb_id": "test-kb"}, None, mock_host)
        
        mock_host.cursor.get.assert_called_once_with("test-kb")

    @pytest.mark.asyncio
    async def test_uses_delta_endpoint_when_cursor_exists(self, plugin, mock_host):
        """Test plugin uses delta endpoint when cursor exists."""
        delta_url = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$deltatoken=abc123"
        mock_host.cursor.get.return_value = delta_url
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [],
            "@odata.deltaLink": "https://graph.microsoft.com/delta?token=xyz"
        }
        
        result = await plugin.execute({"op": "ingest", "kb_id": "test-kb"}, None, mock_host)
        
        assert result.status == "success"
        # Verify delta endpoint was used
        call_args = mock_host.http.fetch.call_args_list[0]
        url = call_args.kwargs['url']
        assert "delta" in url

    @pytest.mark.asyncio
    async def test_processes_deleted_messages(self, plugin, mock_host):
        """Test delta sync processes @removed messages by calling delete_ko."""
        mock_host.cursor.get.return_value = "https://graph.microsoft.com/delta?token=abc"
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [
                {"id": "msg_deleted_001", "@removed": {"reason": "deleted"}}
            ],
            "@odata.deltaLink": "https://graph.microsoft.com/delta?token=xyz"
        }
        
        result = await plugin.execute({"op": "ingest", "kb_id": "test-kb"}, None, mock_host)
        
        assert result.status == "success"
        assert result.data["deleted"] == 1
        mock_host.kb.delete_ko.assert_called_once_with(external_id="msg_deleted_001")

    @pytest.mark.asyncio
    async def test_stores_delta_token_after_success(self, plugin, mock_host):
        """Test plugin stores delta token via host.cursor.set."""
        mock_host.cursor.get.return_value = "https://graph.microsoft.com/delta?token=old"
        new_token = "https://graph.microsoft.com/delta?token=new"
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "value": [],
            "@odata.deltaLink": new_token
        }
        
        result = await plugin.execute({"op": "ingest", "kb_id": "test-kb"}, None, mock_host)
        
        assert result.status == "success"
        mock_host.cursor.set.assert_called_once_with("test-kb", new_token)
        assert result.data.get("history_id") == new_token

    @pytest.mark.asyncio
    async def test_handles_410_gone_with_fallback(self, plugin, mock_host):
        """Test 410 Gone triggers fallback to full sync."""
        mock_host.cursor.get.return_value = "https://graph.microsoft.com/delta?token=expired"
        mock_host.http.fetch.side_effect = [
            {"status_code": 410, "error": {"message": "Delta token expired"}},
            {"status_code": 200, "value": [], "@odata.nextLink": None},
            {"status_code": 200, "value": [], "@odata.deltaLink": "https://graph.microsoft.com/delta?token=new"}
        ]
        
        result = await plugin.execute({"op": "ingest", "kb_id": "test-kb"}, None, mock_host)
        
        assert result.status == "success"
        mock_host.cursor.delete.assert_called_once_with("test-kb")

    @pytest.mark.asyncio
    async def test_reset_cursor_forces_full_sync(self, plugin, mock_host):
        """Test reset_cursor=True bypasses existing cursor."""
        mock_host.cursor.get.return_value = "https://graph.microsoft.com/delta?token=abc"
        mock_host.http.fetch.side_effect = [
            {"status_code": 200, "value": [], "@odata.nextLink": None},
            {"status_code": 200, "value": [], "@odata.deltaLink": "https://graph.microsoft.com/delta?token=new"}
        ]
        
        result = await plugin.execute(
            {"op": "ingest", "kb_id": "test-kb", "reset_cursor": True},
            None,
            mock_host
        )
        
        assert result.status == "success"
        # cursor.get should NOT be called when reset_cursor is True
        mock_host.cursor.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_tracks_both_ingestion_and_deletion_counts(self, plugin, mock_host):
        """Test delta sync tracks both added and deleted counts."""
        mock_host.cursor.get.return_value = "https://graph.microsoft.com/delta?token=abc"
        mock_host.http.fetch.side_effect = [
            # Delta response with 1 new and 1 deleted
            {
                "status_code": 200,
                "value": [
                    {"id": "msg_new", "subject": "New", "from": {"emailAddress": {"name": "J", "address": "j@e.com"}}, "receivedDateTime": "2024-01-15T10:00:00Z"},
                    {"id": "msg_deleted", "@removed": {"reason": "deleted"}}
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/delta?token=xyz"
            },
            # Full message fetch
            {
                "status_code": 200,
                "id": "msg_new",
                "subject": "New",
                "from": {"emailAddress": {"name": "J", "address": "j@e.com"}},
                "toRecipients": [],
                "ccRecipients": [],
                "bccRecipients": [],
                "receivedDateTime": "2024-01-15T10:00:00Z",
                "body": {"contentType": "text", "content": "Body"}
            }
        ]
        
        result = await plugin.execute({"op": "ingest", "kb_id": "test-kb"}, None, mock_host)
        
        assert result.status == "success"
        assert result.data["count"] == 1
        assert result.data["deleted"] == 1
